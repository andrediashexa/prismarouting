# Interface configuration

Everything configured inside an interface: addresses, VLAN, VRF, MTU, MAC, administrative state, source filtering, the CGNAT side, VRRP, OSPF and IS-IS on the port, loopbacks and LACP bundles. For whoever is bringing up a link, terminating subscribers on VLANs or investigating a port that passes no traffic.

You enter an interface from configuration mode, and leave with `exit`:

```
prisma> configure
prisma# [edit] interface ens18
prisma# [edit interface ens18]
```

The prompt says which interface you are in. `?` or TAB list what is valid in there and nothing else.

Changing an interface requires the **administrator** capability. The exception is the CGNAT side (`cgnat inside|outside`), which a `cgnat-operator` account can also change.

## What an interface understands

| Command | Argument | What it does |
|---------|----------|--------------|
| `description` | `<text>` | what the interface is for, up to 63 characters; appears in `show interface description` |
| `shutdown` |          | brings the interface down administratively |
| `no shutdown` |          | brings it back |
| `ipv4 address` | `<prefix>` | IPv4 address; can repeat |
| `ipv6 address` | `<prefix>` | IPv6 address; can repeat |
| `no ipv4 address` | `<prefix>` | removes **one** address, without touching the others |
| `mtu`   | `<576-9216>` | MTU, on the interface and on its Linux pair; without it, 1500 |
| `linux-name` | `<name>` | **another** name by which routing and `ip` see this interface; without it, every interface already enters Linux with its own name |
| `vrf`   | `<1-4294967295>` | routing table where the addresses live; `0` takes the interface out of one |
| `vlan`  | `<1-4094> [inner <1-4094>]` | sub-interface tag |
| `mac-address` | `<xx:xx:xx:xx:xx:xx>` | the MAC the interface answers to |
| `cgnat` | `inside\|outside` | this interface's side in CGNAT: `inside` is where subscribers come in, `outside` is where translated traffic leaves |
| `no cgnat` |          | takes the interface out of CGNAT |
| `no ipv6 nd suppress-ra` |          | advertises this box as a router on this port (by default it does not) |
| `ipv6 nd suppress-ra` |          | back to the default: does not advertise |
| `ipv6 nd nat64` | `[<X:X::X:X/M>]` | puts the NAT64 prefix in the advertisements (PREF64, RFC 8781), for the subscriber's CLAT; without a prefix, `64:ff9b::/96`; can repeat. See *The NAT64 prefix in the advertisements* below |
| `no ipv6 nd nat64` | `[<X:X::X:X/M>]` | removes that prefix from the advertisements, or all of them |
| `urpf`  | `strict\|loose\|off` | drops packets whose source does not route back through here |
| `vrrp`  | `<1-255> address <A.B.C.D>` | a service address this box contends for with its peer (RFC 5798); IPv4, no mask; can repeat |
| `vrrp`  | `<1-255> priority <1-254>` | who keeps the address when both are healthy; higher wins |
| `vrrp`  | `<1-255> track <interface> decrement <1-254>` | how much the priority drops when that other port goes down |
| `no vrrp` | `<1-255>` | removes the whole virtual router from this interface |
| `member`, `lacp`, `load-balance` |          | only on an `ae<n>` bundle; see *LACP bundles* below |
| `ip ospf ...`, `ipv6 ospf6 ...` |          | OSPF and OSPFv3 on this interface; see *OSPF and IS-IS on the interface* below |
| `ip router isis`, `ipv6 router isis` | `<instance>` | IS-IS on this interface, for IPv4 and for IPv6 |
| `isis ...` |          | IS-IS settings on this interface |
| `exit`  |          | leaves the interface |

## MTU

An interface without `mtu` stays at **1500**, the box default since 0.152.1, and `no mtu` goes back to it. This also applies to what `commit` creates: a VLAN, a loopback, a bundle. On a card that cannot handle 1500, the default is the card's maximum.

```
interface ens18
 mtu 9000
 exit
```

`mtu` changes the dataplane interface and its Linux pair, together. A pool's `tcp-mss auto` is derived from the MTU of the CGNAT interfaces: at 1500 it gives 1460.

On a box installed **before** 0.152.1 the interfaces remain at 9000: the upgrade wrote `mtu 9000` on each one that had no `mtu`, and the card keeps accepting frames of up to 9000. There, `no mtu` also takes the interface to 1500.

## Addresses

```
interface ens18
 ipv4 address 200.150.0.1/24
 ipv4 address 200.150.1.1/24
 ipv6 address 2001:db8::1/64
 exit
```

The keyword and the prefix have to match. `ipv6 address 10.0.0.1/24` is refused:

```
error: '10.0.0.1/24' is not an IPv6 prefix
```

This is not pedantry. Without the check the address would be accepted and configured the wrong way, silently.

`no ipv4 address 200.150.1.1/24` removes **that** address. The others stay.


:::info
When replacing one address with another, the old one goes out before the new one comes in — whoever writes both lines in the same commit ends up with one address, not with two for an instant.

:::

## The name by which routing sees the port

The dataplane and the operating system are two worlds, and routing lives in the second. What links them is the pair: the interface through which the kernel, `ip` and FRR see the dataplane port.

**Every interface enters Linux with its own name, on its own.** As soon as an interface has an `interface` block - with anything inside, even just a `no shutdown` -, it appears in `ip` and in routing with the same name. This applies to cards, sub-interfaces, loopbacks and cards added while the box is running. The dataplane's `ens18` is FRR's `ens18`, and none of this appears in the configuration: there is no line to write.

The name belongs to Linux, and its rules apply: at most 15 characters and no slash. A card nobody named keeps the spelling the dataplane gives it, full of slashes, and Linux does not accept that one. For that case, and for whoever wants a different name, there is `linux-name`:

```
interface ens18
 linux-name vppwan0
 ipv4 address 200.150.0.1/24
 exit
```

Only in that case does the line appear in the configuration: when it gives a name **different** from the interface's. A name Linux does not accept is refused, saying why:

```
error: linux-name: 'GigabitEthernet0/12/0' cannot be a Linux interface name (15 characters, no '/')
```

The pair is **read back** from the box, not remembered by the VTY. A configuration saved by an earlier version, which carries `linux-name ens18` in `interface ens18`, is read normally, and the next `save` writes the file without the line. The box stays exactly the same.

The box does not take a name Linux already uses for something else: requesting the pair with a name in use fails, and would bring down the whole `commit`.

A port whose name routing does not accept - more than 16 characters, which is what a card without a name gets from the dataplane - is refused at `commit`, with the limit in the sentence, before anything is applied. On an installed box this does not happen: every claimed card carries the name it had.

## Loopbacks

An address on a physical port disappears together with the port. A BGP, OSPF or IS-IS `router-id`, an iBGP `update-source` that survives a link going down, an SNMP or log source address that does not move — they all want an interface that is always up.

**Naming a loopback is what creates it**, the same way as naming a sub-interface: there is nothing else on the box to create it from.

```
configure
 interface loop0
  ipv4 address 10.99.0.1/32
  description router-id, que uma porta não leva junto
  exit
 commit
```

The loopback enters Linux and routing with its own name, `loop0`, without any extra line — like any other interface.

`loop<n>` is the dataplane's own spelling, and the number you write is the number it gets: `loop3` produces `loop3`, and not the next free one. That way the configuration and `show interface` cannot disagree about what the thing is called.

`no interface loop0` undoes the whole path — address, pair and interface. A **physical port** is still refused: it is not something a configuration can remove, and accepting the words would promise what `commit` does not deliver.

## LACP bundles (802.3ad)

Two or more ports become a single interface, `ae<n>`, as in Junos. The bundle is created when it is named, like a loopback, and each port joins with `member`:

```
configure
 interface ae0
  member ens19
  member ens20
  lacp rate fast
  ipv4 address 10.9.0.1/24
  exit
 commit
```

| Command | What it does | Default |
|---------|--------------|---------|
| `member <port>` | a port in the bundle; can repeat |         |
| `lacp mode active\|passive` | who starts the negotiation | `active` |
| `lacp rate fast\|slow` | LACP every second or every 30 | `slow`  |
| `load-balance l2\|l23\|l34` | how flows spread across the members | `l34`   |
| `no member <port>` | takes the port out of the bundle |         |
| `no interface ae0` | undoes the bundle; the ports go back to being ports |         |

Addresses, MTU, description, OSPF, BGP and the CGNAT side go on `ae0`, never on the members. `commit` refuses a member the box does not have, a port in two bundles and a member with configuration of its own.

Changing `lacp mode`, `lacp rate` or `load-balance` rebuilds the bundle: load balancing is fixed at creation, and the mode and rate when each member joins. That is why the change brings the bundle down for an instant, and it is better done in a window.

`show lacp` shows the state of each member. A member that is in the bundle appears with `MUX-state: COLLECTING_DISTRIBUTING`.

Measured in a lab against a Linux 802.3ad bond: both members negotiated and stayed aggregated on both sides, traffic crossed the bundle and kept crossing with one member cut.

## Sub-interfaces and VLAN

**How to create one, from start to finish.** Just type a name the box does not have yet, in the `<parent>.<tag>` format: that is what creates the sub-interface. `?` says so on the line below the interface list — it offers the ones that exist, and the note underneath says which names create a new one.

```
prisma> configure
prisma# [edit] interface ens18.100
prisma# [edit interface ens18.100] vlan 100
prisma# [edit interface ens18.100] ipv4 address 100.64.0.1/22
prisma# [edit interface ens18.100] exit
prisma# [edit] commit
commit complete
```

After that the sub-interface exists in the dataplane, carries the tag, and gets its own Linux pair with the same name, like every interface that has a block — so Linux and routing see it with its address:

```
prisma> show interface
  Interface                  State  MTU    Address             Linux
  ens18                      up     1500   10.95.200.198/24    tap4096 ens18
  ens18.100                  up     1500   100.64.0.1/22       tap4096.100 ens18.100
```

**If the parent is** `**shutdown**`**, the sub-interface waits — and the** `**commit**` **goes through anyway.** VPP does not bring a sub-interface up while the parent interface is down, so the box does not try and says so:

```
warning: interface ens19.100: left shut down
    the dataplane will not bring a sub-interface up while its parent is down,
    and ens19 is shut down; 'no shutdown' on ens19 brings both up
```

A `no shutdown` on the parent brings both up. It is a warning and not a refusal: `commit` applies the rest normally.

**The parent must exist.** `interface ens99.100` on a box without `ens99` is refused at `commit`, naming the parent:

```
error: interface ens99.100: its parent ens99 is not on this box
error: candidate did not pass validation; nothing applied
```

A sub-interface is an interface: everything in the table above applies inside it.

```
interface ens18.100
 vlan 100
 ipv4 address 100.64.0.1/22
 exit

interface ens18.200
 vlan 200 inner 10
 exit
```

`no interface ens18.100` removes the sub-interface **and what was on it** — an address left behind would describe a port that does not exist.


:::warning
**The number after the dot must equal the outer tag.** `ens18.7` with `vlan 100` is refused.

The box names the sub-interface by the tag, and the running configuration is read from the box. If the two names could diverge, you would read one name and the box would have another.

:::


:::warning
**The tag goes from 1 to 4094, in both positions.** `vlan 0`, `vlan 4095` and any number above that are refused as you type - and the same goes for the inner tag, `vlan 100 inner 0` and `vlan 100 inner 4095`.

```
error: vlan 4095 out of range (1-4094): 802.1Q reserves 0 and 4095
```

Zero and 4095 are reserved by 802.1Q itself: zero is priority tagging, a frame that carries the tag header and no VLAN inside, and 4095 is not assignable. No switch on the other side delivers either one, so a sub-interface built on top of them looks configured and carries nothing.

:::

On a double-tagged sub-interface (`inner`), frames arriving with **more** than two tags and the same outer/inner pair also match. If your design depends on matching exactly two, confirm the behavior before putting it into production.

## VRF

```
interface ens18
 vrf 7
 ipv4 address 10.7.0.1/24
 exit
```

The table is numeric, like a pool's — one number, one meaning. Table 0 is the default and does not need to be written.

Moving an interface to another VRF **takes the addresses with it**: they are removed, the interface changes table, and the addresses come back. You write only the `vrf` line; the rest follows.

### The Linux VRF: `vrf <name> table <n>`

```
vrf red table 10
interface ens18
 vrf 10
 ipv4 address 10.10.0.1/24
 exit
```

Routing calls a VRF by **name** — `router bgp 65000 vrf red`, the `vrf red` block with its statics — and finds its table through a Linux VRF device with that name. `vrf red table 10` creates that device on table 10, the same as the interfaces' `vrf 10`, and puts into it the Linux side of each of those interfaces (the linux_cp pair, or the `linux-name`). After the commit the VRF's BGP comes up without any step in Linux.

| You do | In Linux |
|--------|----------|
| `vrf red table 10` | creates `red` on table 10 and brings it up |
| `vrf 10` on an interface | its Linux side joins `red` |
| `no vrf` on an interface | its Linux side leaves `red` |
| `no vrf red table` | removes `red`; the interfaces leave with it |

Without `table`, `vrf red` is still the routing block, as before.

Refused: names starting with `prisma-` (`prisma-bp` belongs to the box itself), the name `default`, Linux's own tables 253 to 255, a table that already belongs to another VRF and the `prisma-bp` table (128). `prisma-bp` does not appear in `show running-config` and is never touched.

VRFs appear in `show running-config` read from Linux, and the saved file creates them again at boot, before routing needs them.

### Management in its own VRF

For the main table to carry the subscribers' default and management to remain reachable through its own gateway, the management card goes into a VRF like any interface, with its routes (both defaults, IPv4 and IPv6, and any other management network) in the routing `vrf` block, and `system` > `management` > `vrf` tells the box that its services live there:

```
vrf mgmt table 100
interface enp6s18
  vrf 100
  exit
vrf mgmt
 ip route 0.0.0.0/0 10.40.200.1
 ip route 10.95.200.0/24 10.40.200.1
 ipv6 route ::/0 2804:4ed4:1000:6::1
 exit
router static
 0.0.0.0/0 next-hop 10.40.10.29
 exit
system
 management
  vrf mgmt
  exit
 exit
```

`router static` is only the main table. A VRF's static route goes in its `vrf <name>` block, closed with `exit` or `exit-vrf`.

Do it with `commit confirmed 10` and confirm with `commit` after logging in again through the management address, over IPv4 and over IPv6. If the session drops, nobody confirms and the rollback undoes everything: the VRF, the rules and the kernel settings. The box goes back to answering through the main table.

Up to 0.152.5, right after the card joined the VRF its IPv6 address could go unanswered for more than 20 seconds, sometimes until the box spoke to the gateway first: the dataplane dropped neighbor solicitations arriving by multicast for an interface moved to another table. From 0.152.6 on it answers the very first one, in this VRF and in any other.

Up to 0.152.6, on a busy box, removing the VRF (a `commit confirmed` that expired, for example) and putting it back could leave the VRF's IPv6 default out of Linux until FRR restarted: zebra kept the connected route of the inactive card. From 0.152.7 on the box moves the card to the VRF with no address on it, waits for zebra to catch up before giving the addresses back, and only removes a VRF after zebra lets go of it. This applies to management and to any customer's VRF. If zebra takes more than 10 seconds, the commit proceeds and warns, and `show log` keeps what zebra still had.

**A management card the dataplane does not own** (from 0.155.19). In a virtual machine the management card is usually a virtio NIC that Linux keeps next to the cards the dataplane takes, such as `ens18` with the address the installer put in `/etc/network/interfaces.d/prisma-routing`. It goes into the VRF the same way, with `interface ens18` and `vrf 100`: the box moves only its Linux side, takes its addresses and the installer's default route along, and puts that default into the VRF's table as the way back. A `commit confirmed` that expires takes it out again, with the default back in the main table, and SSH answers there as before. Up to 0.155.18 that commit passed `commit check` and failed when applied (`the dataplane does not have it`), and with management in the main table the installer's default won over the `router static` one: the dataplane was left without a default for the subscribers. On such a card the block takes only `vrf`; addresses, MTU and description stay in Linux, and `commit check` refuses them, as it refuses a name that neither the dataplane nor Linux has.

| What | Which way it goes |
|------|-------------------|
| SSH, API, SNMP and prisma-web coming in, IPv4 and IPv6 | through the management VRF; the reply follows the connection |
| DNS, NTP, the `system upgrade` download, traps, `system logging`, the log container's export | through the management routes, unless the main table has a specific route to the destination |
| IPFIX over UDP generated by the dataplane | through the main table: VPP sends it on its own. A collector behind management needs a specific route in the main table, or the TCP export |

Without `system` > `management` > `vrf mgmt` the card stays in the VRF and the box does **not** answer through it: the services listen on the main table, and the snmpd and sshd that listen on the management address do not even come up. With it, the box turns on `tcp_l3mdev_accept`/`udp_l3mdev_accept`, `ip_nonlocal_bind` (IPv4 and IPv6) and IPv6 `keep_addr_on_down`, and puts in two rules per family for its own traffic - at commit and at every boot, before snmpd, sshd and the API come up. `show system management` shows what is configured and what is in effect on the host. `no vrf` inside `management` puts everything back on the main table. `commit check` refuses a VRF the configuration does not create, or one with no interface in it.

For SSH to answer only on management, IPv4 and IPv6, list both its addresses in `ssh listen-address` (since 0.152.5), as with `snmp` and `api`: `ssh listen-address 10.40.200.166 2804:4ed4:1000:6::166`. With management in the VRF, `commit` accepts these addresses even though they are in the VRF, because `ip_nonlocal_bind` lets sshd listen on them; without `vrf` under `management`, it refuses.

## Administrative state

`shutdown` brings down both sides: the interface that forwards packets and the pair routing sees. Bringing down only one would leave the routing protocol holding an adjacency over a port that stopped forwarding — the neighbor believes the link is fine until its own timer expires, and traffic vanishes in the meantime.

In a single commit, what goes down is applied **before** the other changes and what comes up **after**: an interface being brought down stops carrying traffic before the configuration changes underneath it, and one coming up comes up already configured.

## IPv6 router advertisement

An interface with an IPv6 address does **not advertise** this box as a router. That is the default, and it is deliberate: on a subscriber-facing port, advertising would be a second gateway for everyone who receives a prefix via DHCPv6-PD, next to the router the subscriber already uses.

On the ports where advertising makes sense — an uplink, a transit, a port facing other routers — turn it on by exception:

```
interface ens18
 no ipv6 nd suppress-ra
 exit
```

When on, the advertisement goes out every 200 seconds with a 600-second lifetime, plus three initial advertisements right after it is turned on. `show running-config` shows the line only on the interfaces where it was written, because on the others it is the default.


:::info
`ipv6 nd suppress-ra` is also accepted. It changes nothing — it states out loud what already applies — and serves to leave the intent written in the configuration, or to undo the exception on an interface where it was turned on.

:::

## The NAT64 prefix in the advertisements (PREF64, 464XLAT)

On an IPv6-only access served by a NAT64 pool, the subscriber still has programs that only speak IPv4: a game, a VPN client, an app that opens a connection to an IPv4 address written out in full. 464XLAT (RFC 6877) solves this with a **CLAT** on the subscriber's side — on their router or on their phone —, which translates that IPv4 into IPv6 addressed to the NAT64 prefix, and this box's NAT64 pool translates it back. For that the CLAT needs to know **which** prefix it is, and on a network with more than one pool, or with a prefix other than `64:ff9b::/96`, it has no way to guess.

The box tells it, in the router advertisements themselves — the RFC 8781 PREF64 option —, on the subscriber-facing interface:

```
interface ens19
 no ipv6 nd suppress-ra
 ipv6 nd nat64 64:ff9b:1::/48
 exit
```

The prefix is the `nat64-prefix` of a NAT64 pool on this box (see [Pool configuration](/s/wiki-en/doc/pool-configuration-wkJ1DcmG2Y)). Without a prefix, `ipv6 nd nat64` advertises `64:ff9b::/96`; the line can repeat, one prefix on each. The accepted lengths are those of RFC 6052: `/32`, `/40`, `/48`, `/56`, `/64` or `/96`.

`commit check` refuses two things, each saying what to do:

* `ipv6 nd nat64` on an interface that **does not advertise** — without `no ipv6 nd suppress-ra` there is no advertisement to carry the prefix;
* a prefix that **no NAT64 pool translates** — a CLAT that learned that prefix would send the subscriber's IPv4 to a destination the box drops. The refusal lists the prefixes this box's pools use.

**Whoever sends this advertisement is FRR, not the dataplane.** VPP's advertisement does not have the PREF64 option; FRR's does, and it goes out the same port. On an interface with `ipv6 nd nat64` the box hands the advertisement to FRR and turns off the dataplane's, so the subscriber hears **one** router only, with the prefix in every advertisement. Removing `ipv6 nd nat64`, the advertisement goes back to the dataplane. You do not need to write anything in FRR: the lines belong to the box, `show running-config` shows them inside the interface block and never in the routing part.


:::info
The CLAT can also learn the prefix by asking a DNS64 resolver for `ipv4only.arpa` (RFC 7050). This box is not the subscriber's resolver: the DNS64 you operate needs to synthesize with **the same** prefix the box advertises, or the two methods disagree.

:::

## A pair of boxes and the address that moves from one to the other

Two boxes, one service address, and the subscriber pointed at it instead of at either of the two. It is RFC 5798 VRRP, and here it runs **on the dataplane interface**, which is where packets arrive:

```
interface ens18
 ipv4 address 100.64.3.252/22
 vrrp 1 address 100.64.3.254
 vrrp 1 priority 200
 vrrp 1 track ens19 decrement 150
 exit
```

On the other box, the same with its own address and a lower priority:

```
interface ens18
 ipv4 address 100.64.3.251/22
 vrrp 1 address 100.64.3.254
 vrrp 1 priority 100
 vrrp 1 track ens19 decrement 150
 exit
```

**The** `**vrid**` **must be the same on both boxes**, and different from that of any other pair in the same broadcast domain: it is what says which election each advertisement is about, and the virtual MAC comes from it (`00:00:5e:00:01:<vrid>`).

**The virtual MAC is what keeps the subscriber from needing to know anything.** Whoever takes over the address also takes over that MAC and sends a gratuitous ARP. The CPE that had that address in its ARP table keeps sending to the same MAC, and the frame starts arriving at the other box — without anyone clearing a cache anywhere.

`**priority**` **is your statement of who should hold the service.** Higher wins, and the box coming back from maintenance **takes the address back**, because that is what the configuration says. 255 is refused: the RFC reserves that value for the address owner, and a box that shares the address with another is not one.

`**track**` **is the failure nothing else covers.** A box with its uplink pulled stays alive and keeps advertising: it holds the gateway address and reaches nothing. With `track`, its priority drops by the decrement and the peer takes over. Choose the decrement so that the drop takes the priority **below** the other's — 200 with a decrement of 150 becomes 50, against the peer's 100.

**The same** `**vrid**` **serves IPv6** (from version 0.153.1 on). An IPv6 address in the same `vrrp <vrid>` creates the IPv6 virtual router (VRRPv3, RFC 5798) alongside the IPv4 one, with the same priority, the same `track` and the same preempt — so the subscribers' IPv6 gateway changes boxes together with the IPv4 side:

```
interface ens18
 ipv6 address 2001:db8:1200:ab00::2/56
 ipv4 address 100.64.3.252/22
 vrrp 1 address 100.64.3.254
 vrrp 1 address fe80::1
 vrrp 1 address 2001:db8:1200:ab00::1
 vrrp 1 priority 200
 vrrp 1 track ens19 decrement 150
 exit
```

The IPv6 router needs **one** link-local address (`fe80::...`), which is the first one it advertises and the gateway the subscribers point to (RFC 5798, section 5.2.9); `commit check` refuses an IPv6 router without it, and a second link-local is refused on the spot. The IPv6 virtual MAC is `00:00:5e:00:02:<vrid>`, and whoever takes over sends an unsolicited Neighbor Advertisement. `show cgnat ha` shows both routers of the same `vrid`, the IPv6 one marked `ipv6`.

**The** `**track**` **applies to the router's family.** The dataplane only considers a tracked interface "up" if it has an address of the router's family. The box enables IPv6 (link-local only, without router advertisement) on the interface an IPv6 router tracks; for the IPv4 router, the tracked interface needs an IPv4 address, and `commit check` warns when it has none — an IPv6-only subscriber side tracked by the uplink's IPv4 router would never be seen as up.

**What is refused as you type:** an address with a mask (`100.64.3.254/22` — the virtual address is one of the addresses of the interface's subnet, not a subnet), priority 255, and an interface tracking itself: a port that went down has already stopped advertising, and what is worth tracking is the **other** side.

**Each side wants its own.** An address that moves on the subscriber side and nothing on the uplink side is a failover the return traffic does not follow: configure a virtual router on each side, with a different `vrid`.

### What the virtual address does not do

**It does not answer ping.** Whoever is not the address owner does not answer ICMP for it, which is the default behavior of RFC 5798 (section 6.4.3) — and is what this box does. Monitoring should ping each box's **own** address, which is what says whether that box is alive; the virtual address only says which of the two holds it. Forwarding **through** it works normally, and that is what it exists for.

**It does not synchronize any session.** Ongoing connections drop on failover, as they do with any NAT. What does not change is the subscriber's public address, because in deterministic mode the mapping is a function of the configuration — the high availability page explains what this preserves and what it does not.

### Seeing each box's role

```
prisma> show cgnat ha
  this box
    mapping fingerprint  e3b0c44298fc1c14
    pools                residencial

  role
    ens18 vrrp 1   master, holding the address
      address(es)        100.64.3.254
      virtual mac        00:00:5e:00:01:01
      priority           200
```

When some tracked port is down, the priority line says both things — what is being advertised and what was configured — because a box advertising less than what was configured is a box with a cable out, and not a VRRP problem.

## OSPF and IS-IS on the interface

Routing protocols come onto the port through statements inside the `interface` block itself, which is where FRR keeps them. These go to routing word for word; those in the table at the top of the page are applied by the box in the dataplane.

```
interface ens19
 ip ospf area 0
 ip ospf network point-to-point
 ip ospf cost 10
 ip router isis CORE
 isis network point-to-point
 isis metric 100
 exit
```

### OSPF (`ip ospf ...`)

| Command | Argument | What it does |
|---------|----------|--------------|
| `ip ospf area` | `<area>` | runs OSPF on this interface, in this area |
| `ip ospf cost` | `<1-65535>` | the cost of this interface |
| `ip ospf network` | `point-to-point\|broadcast` | `point-to-point` skips the DR election, which is what a transit link wants; `broadcast` is the default on Ethernet |
| `ip ospf hello-interval` | `<1-65535>` | seconds between hellos |
| `ip ospf dead-interval` | `<1-65535>` | seconds until the neighbor is declared down |
| `ip ospf priority` | `<0-255>` | this box's priority in the DR election |
| `ip ospf authentication` | `message-digest\|null` | MD5, or none, overriding the area's |
| `ip ospf authentication-key` | `<key>`  | the plain-text key; never shown back |
| `ip ospf message-digest-key` | `<1-255> md5 <key>` | an MD5 key; never shown back |
| `ip ospf passive` |          | runs OSPF here and sends no hello |
| `ip ospf bfd` |          | follows the adjacency with BFD |
| `ip ospf mtu-ignore` |          | does not compare MTU in the database exchange — the way out when both sides disagree and cannot be made equal |

### OSPFv3 (`ipv6 ospf6 ...`)

| Command | Argument | What it does |
|---------|----------|--------------|
| `ipv6 ospf6 area` | `<area>` | runs OSPFv3 on this interface, in this area — it is the **only** way to put an interface in OSPFv3 |
| `ipv6 ospf6 cost` | `<1-65535>` | the cost of this interface |
| `ipv6 ospf6 network` | `point-to-point\|broadcast` | as in OSPF   |
| `ipv6 ospf6 passive` |          | runs OSPFv3 here and sends no hello |
| `ipv6 ospf6 bfd` |          | follows the adjacency with BFD |
| `ipv6 ospf6 mtu-ignore` |          | does not compare MTU |

`ipv6 ospf6 hello-interval`, `dead-interval` and `priority` are also accepted, in FRR's spelling, but `?` does not offer them: typed at the default value, FRR does not keep them.

### IS-IS

| Command | Argument | What it does |
|---------|----------|--------------|
| `ip router isis` | `<instance>` | runs IS-IS for IPv4 on this interface |
| `ipv6 router isis` | `<instance>` | runs IS-IS for IPv6 on this interface, in whatever topology the instance has |
| `isis circuit-type` | `level-1\|level-2-only\|level-1-2` | which levels this interface takes part in |
| `isis network point-to-point` |          | no DIS election or pseudo-node; configured on one side only, the adjacency does not come up |
| `isis metric` | `<0-16777215> [level-1\|level-2]` | this interface's metric; above 63 requires `metric-style wide` on the instance |
| `isis passive` |          | advertises the interface and sends no hello |
| `isis hello-interval` | `<1-600>` | seconds between hellos |
| `isis hello-multiplier` | `<2-100>` | how many lost hellos until the neighbor drops |
| `isis password` | `clear\|md5 <password>` | this interface's authentication; never shown back |
| `isis bfd` |          | follows the adjacency with BFD |

Keys and passwords appear as `<set, not shown>` in `show this`, `show configuration` and `show running-config`. The saved configuration and the rollback revisions keep the real value, or a rollback could not restore it.

The protocol block (`router ospf`, `router ospf6`, `router isis`) is in [Routing, SSH, users and SNMP](/s/wiki-en/doc/routing-ssh-users-and-snmp-jjxcPssV72).

## Removing a setting: `no`

`no ?` inside the interface lists what can be removed, and after the keyword the argument keeps being offered - `no urpf ?` shows `strict`, `loose` and `off`.

Removing a setting **is not the same as setting it to the default**: the configuration stops saying it, and the interface keeps what the box already had. `mac-address aa:bb:cc:dd:ee:ff` followed by `no mac-address` returns the interface to its own MAC, which is what is wanted by someone who typed it and changed their mind.

The interface carries two kinds of line: this box's own - `description`, `shutdown`, `mtu`, `urpf`, `vlan`, `vrf`, `mac-address`, `linux-name`, addresses, `ipv6 nd suppress-ra`, `ipv6 nd nat64`, `cgnat`, `vrrp`, `member`, `lacp`, `load-balance` - and the routing protocols' - `ip ospf ...`, `ipv6 ospf6 ...`, `ip router isis`, `isis ...`. `no` works on both, and in each case goes to whoever owns the setting.

### `no vlan` does not exist, and the box says what to do instead

A sub-interface **is** its tag: the name `ens18.700` and VLAN 700 are the same information, and the box refuses to create one where the two disagree. There is, therefore, no sub-interface without a tag - what you want is the sub-interface gone:

Typing `no vlan 700` inside the sub-interface is refused, and the refusal says what to do instead:

```
error: vlan cannot be removed
    a sub-interface is named after its tag, so one without a tag is not
    something the dataplane can hold
    remove the sub-interface itself: no interface <name>
```

The refusal comes on lines of its own, within the console width: the verdict on the first, the reason in the middle, and **what to type instead on the last**. For a while it came out as a single sentence, wider than the screen, and the terminal wrapped it wherever it happened to - which put precisely the closing advice anywhere. Now the last line is always the last line.

`no vlan`, without a number, gets the same refusal: there is no tag to remove either way.

`no interface ens18.700`, at the level above, removes the sub-interface and everything that was on it.

## An interface in detail

`show interface` lists them all. Naming one, the answer changes from a table to a report:

```
prisma> show interface ens18
  ens18
    state       up
    mtu         9000
    addresses   200.150.0.1/24
    linux       tap4096 vppwan0
    vrf         0 (default)
    mac         02:fe:c1:ed:07:aa
    urpf        off
    cgnat       not attached
    counters
      rx bytes                     120
      punt                           2
```

The counters are the reason the report exists. *Up* and *up and dropping four percent of what arrives* are identical in a table, and it is the second one that generates the ticket.

**On a box that is part of a pair, the report gives the port's role**, right below CGNAT:

```
    cgnat       in2out
    vrrp 1      master, holding the address
                100.64.3.254 on 00:00:5e:00:01:01
```

It is the first thing that matters about a port when there is a peer: whether the subscribers are on this box or on the other one. `show cgnat ha` answers that for the whole box; here it is for the port you are already looking at.

What appears is the **role in effect**, read from the dataplane — what was *configured* is in `show this` and in the saved file. A port without a virtual router shows no line at all: most have none, and a notice that always appears is a notice nobody reads.

The exact name takes precedence: asking for `ens18` on a box that also has `ens18.100` returns the interface's report, not the list of both.

## The card handed to the dataplane is not one of these

`interface <name>` configures an interface the dataplane **already has**. Which physical card it receives is another decision, one level below, and it lives at the top of configuration mode:

```
prisma> configure
prisma# [edit] system
prisma# [edit system] dataplane
prisma# [edit system / dataplane] interface enp2s0
prisma# [edit system / dataplane] commit
```

The difference is practical. On a box that has not yet received any card the dataplane has no interface at all, so there is no `interface <name>` to enter — and `show interface` is empty precisely when you need to choose. That is why the name here is the one **Linux** gives the card, and the one listing the candidates is `?` itself.

| Command | Argument | What it does |
|---------|----------|--------------|
| `interface`, under `system` > `dataplane` | `<card>` | hands this card to the dataplane at its next start |
| `no interface`, at the same level |          | the box stops taking cards: a safe state, reachable over the network |

`commit` stores the choice, recomputes the boot tuning and **does not restart the dataplane** — the handover drops every session on the box, including yours, so the window is yours to choose. A name Linux does not have is refused as you type, with the list of the cards it has; the case, with what the box prints, is in [Getting started](/s/wiki-en/doc/getting-started-WksYlCtcGd).

The card that carries the default route is **not** refused: a new box is configured at the console, and it is the boot tuning that, at the boot that hands over the card, reads the return path through it and refuses the card if it cannot read it.

`system dataplane restart`, in operational mode, is something else: it restarts the dataplane that already exists, without changing cards.

## Card added while the box is running

The dataplane only takes cards through DPDK when it **starts**. For a card added in the hypervisor with the box up, the box does not wait for the next boot, and also does not restart the dataplane on its own: a restart brings down the whole CGNAT, with the port blocks and sessions of every subscriber.

Instead it **attaches the card to the running dataplane through AF_XDP**, which reads the card through the kernel driver. The card forwards right away, and nobody loses a session.

**What the box does on its own**, seconds after the card appears:

* checks whether the boot rule would take that card. The card reserved for management, `PRISMA_DATAPLANE_IF` and the card that has an address or the default route remain Linux's, exactly as at boot;
* attaches the card with the **same name** Linux gave it (`ens20`, for example) and with its own MAC.

**What you do** is configure the card like any other: `interface ens20`, the address and `no shutdown`. It enters Linux and routing with its own name, like the others. `show interface` warns, below the count, that it is on AF_XDP and what takes it to DPDK.

**Moving the card to DPDK** is your decision, in the window you choose:

```
prisma> system dataplane restart
```

**Run** `**save**` **first.** What comes back after the restart is the **saved** configuration, and the command warns if there is something committed and not saved. It says what it costs and restarts the dataplane. The card comes back through DPDK, with the same name and the same configuration, together with the other data cards. Measured on a VM: forwarding stopped for 18 seconds and came back without loss. A reboot does the same.


:::info
**The cost of AF_XDP is speed, not correctness.** On a virtio card with a single queue, the kernel itself warns that transmit falls back to a slower mode. For a card that is going to carry subscribers in volume, leave it on AF_XDP only until the next window.

:::

# How to check

* `show interface <name>` gives the report, with state, addresses, VRF, MAC and counters;
* `show interface terse` gives one line per interface with **two** state columns — `Admin`, which is what someone configured, and `Link`, which is what the cable is doing: up/down is a link problem, down/down is `shutdown`;
* `show interface description` shows every interface's description, next to those two columns;
* `show interface utilization <name>` samples the counters and shows how much is going through right now, in bit/s;
* `monitor interface <name>` shows the same live, in a graph per direction redrawn every second until `q`;
* `show running-config` shows the interface block as the box reads it back — what is there is what is in effect, not what was typed.

# See also

* [CGNAT configuration](/s/wiki-en/doc/cgnat-configuration-MXpSfxcJsb) — the `cgnat` level, and what each interface's side means
* [Routing, SSH, users and SNMP](/s/wiki-en/doc/routing-ssh-users-and-snmp-jjxcPssV72) — the `router ospf`, `router ospf6` and `router isis` blocks
* [High availability](/s/wiki-en/doc/high-availability-cMUVBBTFgL) — the pair of boxes and what VRRP preserves
* [uRPF and spoofed sources](/s/wiki-en/doc/urpf-and-spoofed-sources-UlWGqYZ6T6) — when to use `strict` and when `loose`
* [Multi-tenant with VRF](/s/wiki-en/doc/multi-tenant-with-vrf-SUKv4RMvfr) — when each customer has its own table
