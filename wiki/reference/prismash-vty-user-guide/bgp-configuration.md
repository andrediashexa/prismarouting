# BGP configuration

How to configure BGP sessions from `prismash`: neighbors, peer groups, address families and what the box refuses before handing it to routing. For anyone bringing up a transit, a peering or announcing the CGNAT's public prefixes.

You enter BGP from configuration mode:

```
prisma> configure
prisma# [edit] router bgp 65000
prisma# [edit router bgp 65000]
```

`?` or TAB list what is valid in there. Inside an address family they list a different set — they are different grammars, because the same word means different things in the two places.

# A session from start to finish

```
router bgp 65000
 bgp router-id 10.0.0.1
 neighbor 200.150.0.2 remote-as 65001
 neighbor 200.150.0.2 description transito-primario
 address-family ipv4 unicast
  neighbor 200.150.0.2 activate
  network 200.150.8.0/22
 exit-address-family
 exit
```

`remote-as` is what creates the session: without it FRR refuses any other statement for that neighbor, and the box refuses it first, saying which neighbor is missing it.

`activate` is what makes the session **carry** that family. A session that comes up and carries nothing is an Established, silent session.

# What the `router bgp` block understands

| Command | Argument | What it does |
|---------|----------|--------------|
| `bgp router-id` | `<A.B.C.D>` | this box's identifier in BGP |
| `bgp log-neighbor-changes` |          | logs every session state change |
| `bgp graceful-shutdown` |          | drains traffic before maintenance (RFC 8326; its own section below) |
| `bgp default ipv4-unicast` |          | enables IPv4 unicast on every neighbor without needing `activate` |
| `bgp bestpath as-path multipath-relax` |          | treats paths of the same length coming from different ASes as equal |
| `neighbor <neighbor> ...` |          | a session; the next table |
| `address-family ipv4 unicast` or `ipv6 unicast` |          | enters the address family; you leave with `exit-address-family` or `exit` |
| `network` | `<prefix> [policy <policy>]` | a prefix this box originates; it is only announced if it is in the routing table |
| `aggregate-address` | `<prefix> [summary-only] [policy <policy>] [suppress-policy <policy>]` | one route that sums up the more specific ones it covers |
| `table-policy` | `<policy>` | which of BGP's best routes go to the forwarding table |
| `redistribute` | `connected`, `static`, `ospf` or `isis` | injects routes from another source into BGP |

At the `address-family` level, `neighbor` (the per-family list, below), `network`, `aggregate-address`, `table-policy`, `vrf-import`, `redistribute` and the exits `exit-address-family` and `exit` are valid. The forms with a policy, and `vrf-import` between VRFs, are in [Routing policy](/s/wiki-en/doc/routing-policy-policy-options-WXZOz2enzp).

# What a neighbor understands

| Command | Argument | What it does |
|---------|----------|--------------|
| `remote-as` | `<1-4294967295>`, `internal` or `external` | the AS on the other side; this is what creates the session. `internal` is your own AS, `external` any other |
| `description` | `<text>` | what the session is for, for whoever reads it later |
| `password` | `<text>` | the session's MD5 password; never printed back |
| `update-source` | `<address>` or `<interface>` | where the session is sourced from |
| `ebgp-multihop` | `<1-255>` | hops to a neighbor that is not on a connected network |
| `timers` | `<keepalive> <hold>` | session timers |
| `maximum-prefix` | `<n> [<pct>] [restart <min>]` | drops the session above that number of prefixes |
| `next-hop-self` |          | announces this box as the next hop |
| `bfd`   | `[profile <name>]` | tracks the session via BFD, so failure takes milliseconds rather than minutes; with `profile`, using that profile's timers (see [BFD](/s/wiki-en/doc/routing-ssh-users-and-snmp-jjxcPssV72), BFD section) |
| `shutdown` |          | holds the session down, keeping the configuration |
| `peer-group` | `<name>` | puts the neighbor in a peer group |
| `capability extended-nexthop` |          | IPv4 routes over an IPv6 session; it is what a session without an address on the link uses |

Inside `address-family`, the same neighbor understands `activate`, `route-map`, `prefix-list`, `soft-reconfiguration inbound`, `remove-private-AS`, `default-originate`, `advertise`, `maximum-prefix` and `next-hop-self`. The last two appear in both lists on purpose: they apply per family, and routing accepts both places. `default-originate policy <policy>` and `advertise` are covered in [Routing policy](/s/wiki-en/doc/routing-policy-policy-options-WXZOz2enzp); in the running configuration `advertise` shows up as `advertise-map`, the form in which routing stores it.

That table is what `prismash` **checks**. Routing's BGP understands much more than that, and whatever is not here is passed through unread - which is legitimate, and is why it comes out marked `<- not checked` in `show this` and in `show configuration`. Note that `soft-reconfiguration inbound` is in the address-family list and not in the list above: typed directly inside `router bgp`, it comes out marked.

# Session without an address on the link (BGP unnumbered)

A transit or a peering with a directly connected neighbor does not need a /30 on the link. The session comes up over the link-local address both sides already have, and the neighbor is the **interface**:

```
router bgp 65000
 neighbor vppwan0 interface remote-as external
 address-family ipv4 unicast
  neighbor vppwan0 activate
 exit-address-family
 exit
```

`remote-as` goes on the same line, and accepts `external`, `internal` or the AS number. `external` is the common case: any AS other than yours will do, which is exactly what you want on a point-to-point link where the other side is the one that says who it is.

The peer group form is the same:

```
 neighbor UPLINKS peer-group
 neighbor UPLINKS remote-as external
 neighbor vppwan0 interface peer-group UPLINKS
```

The name there is the name by which **routing** knows the interface — the same one used in `passive-interface` and in the OSPF and IS-IS stanzas. A `linux_cp` interface answers to more than one name, and whoever writes the dataplane name gets a warning saying what the other one is:

```
warning: FRR knows this interface as vppwan0, not host-veth-vpp: written this
    way the session has nothing to run over
    neighbor host-veth-vpp interface remote-as external
```

Note the order: **the reason comes first and the refused statement comes last, on a line of its own**. It is not style. The statement is what you will search for in `show configuration` and type again, so it has to come out whole and in one piece; the reason is for reading, so it is wrapped to the console width. With both on the same line, a long route-map name or an IPv6 neighbor put the line between 104 and 264 columns, and what was lost in the wrap was half of each.


:::warning
The link must carry the neighbor's router advertisements for routing to discover the link-local address on the other side. That is how a session without an address finds whom to talk to; without it the session stays in Idle with AS 0, even with the whole configuration right. Check with `show bgp summary`.

There is nothing to enable on this side: what arrives on the interface is delivered to routing. If the session stays in Idle with AS 0, what is missing is on the other side or along the way - a neighbor that does not advertise, or a link that does not deliver link-local multicast. `show bgp neighbors <interface>` shows the learned address once it arrives, and `(unspec)` until it has.

:::

# Peer groups

Forty peerings are not forty stanzas. You declare the group, give it the AS, and the members inherit:

```
router bgp 65000
 neighbor IX peer-group
 neighbor IX remote-as 65100
 neighbor 200.150.9.10 peer-group IX
 neighbor 200.150.9.11 peer-group IX
 exit
```

When completing `neighbor` with TAB, the box shows the existing neighbors with the remote AS next to them, and marks which names are groups — a group is not a router, and looking for it as if it were costs time.

## Removing a neighbor

`no neighbor <neighbor>` removes the whole neighbor: its statements in each address family first, and `remote-as` and its peer-group membership last, in one commit. A peer group a member still names is refused, with the members: the routing would take the members with the group. The `no` applies in the block it is typed in - the same address in `router bgp 65000` and in `router bgp 65000 vrf red` is two neighbors.

# BGP in a VRF

A customer VRF has its own BGP, in `router bgp <AS> vrf <name>`, with the same neighbors, families and policies as the main block. The VRF must exist in Linux: `vrf <name> table <n>` creates its device on the same table as the `vrf <n>` of the interfaces in it ([Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn)):

```
configure
 vrf red table 10
 interface enp2s0
  vrf 10
  exit
 router bgp 65000 vrf red
  neighbor 10.10.0.2 remote-as 65010
  address-family ipv4 unicast
   neighbor 10.10.0.2 activate
  exit-address-family
  exit
 commit
```

The local AS is the block's: the same neighbor AS can be eBGP in one block and iBGP in another, and the `maximum-prefix` warning (below) follows that. Routes pass from one VRF to another with `vrf-import`, through a policy; see [Routing policy](/s/wiki-en/doc/routing-policy-policy-options-WXZOz2enzp). Names that begin with `prisma-` belong to the box's own VRFs: `vrf <name> table` refuses them, and no route goes into or out of them by `vrf-import`.

# Routing policy: `policy-options`

The policy is written once, with a name, and applied on each neighbor as `import` (what comes in) or `export` (what goes out). The format is Junos's. Named lists and policies live in `policy-options`:

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list OUTRO-AS
prisma# [edit policy-options / prefix-list OUTRO-AS] 198.51.100.0/24
prisma# [edit policy-options / prefix-list OUTRO-AS] exit
prisma# [edit policy-options] community NOSSA members 65000:100
prisma# [edit policy-options] policy-statement DO-TRANSITO
prisma# [edit policy-options / policy-statement DO-TRANSITO] term sem-outro-as
prisma# [edit policy-options / policy-statement DO-TRANSITO / term sem-outro-as] from prefix-list OUTRO-AS
prisma# [edit policy-options / policy-statement DO-TRANSITO / term sem-outro-as] then reject
```

What each level of `policy-options` accepts:

| Level | Command | What it does |
|-------|---------|--------------|
| `policy-options` | `prefix-list <name>` | creates or edits a list of networks and enters it; inside, one network per line (`<network>/<length>`, no host bits) and `no <network>` to remove |
| `policy-options` | `community <name> members <asn:value>...` | a named set of communities; typing the same name again replaces it |
| `policy-options` | `as-path <name> <expression>` | a named AS-path regular expression |
| `policy-options` | `policy-statement <name>` | creates or edits a policy and enters it |
| `policy-options` | `no <statement>` | removes a list, a community, an as-path or a policy |
| `policy-statement` | `term <name>` | creates or edits a term and enters it |
| `policy-statement` | `no term <name>` | removes the whole term |
| `term` | `from ...`, `set ...`, `then ...` | the three parts, in the table below |
| `term` | `no <statement>` | removes a `from`, a `set` or the `then` |

A name — of a list, community, as-path or policy — has letters, digits, `.`, `_` and `-`, up to 32 characters. A new `then` replaces the previous one in the same term: the last word said wins.

Each `term` is evaluated in the order it was typed, and has three parts:

| Part | What it says |
|------|--------------|
| `from` | what the route must match: `prefix-list <name>`, `route-filter <network> exact\|orlonger\|longer\|upto /<n>`, `community <name>`, `as-path <name>`, `protocol bgp\|ospf\|isis\|static\|direct\|kernel`, `tag <n>`, `metric <n>`. Several of the same type act as "or"; of different types, as "and". Without `from`, the term matches everything |
| `set` | what changes in the route: `local-preference <n>`, `community add\|set\|delete <name>`, `as-path-prepend <asn>...`, `metric <n>`, `tag <n>`, `next-hop <address>` |
| `then` | how the term ends: `accept` or `reject` stop evaluation; `next term`, or no `then`, applies the `set`s and moves on to the next term; `next policy` passes the route to the next policy of a chain |

**A route that no term decided is accepted**, in `import` and in `export`, as in Junos. To reject the rest, use `default-action reject` in the policy, or finish with a term with no `from` and with `then reject`.

The tables above are the summary. Every condition and action, policy chains, `default-action` and the other places a policy applies are in [Routing policy](/s/wiki-en/doc/routing-policy-policy-options-WXZOz2enzp).

The policy is applied inside the BGP address family, per neighbor or peer group:

```
router bgp 65000
 address-family ipv4 unicast
  neighbor 10.0.0.2 import DO-TRANSITO
  neighbor 10.0.0.2 export PARA-O-TRANSITO
 exit-address-family
```

The box translates each applied policy into what routing understands. In the running configuration, the neighbor shows up with `route-map PO-DO-TRANSITO-IN in`. Names starting with `PO-` belong to that translation: a `route-map` or a `prefix-list` typed by hand with that prefix is refused.

Measured in a lab with two links to the same transit and a third AS behind it:

* the `import` with `then reject` removed the other AS's prefix from the link;
* `set local-preference 200` on one link made it the chosen path, and the dataplane started forwarding only over it;
* the `export` with `as-path-prepend` and `community add` reached the neighbor that way;
* without the `import` and `export` lines, none of this happened.

## Checking a policy: `show policy` and `test policy`

`show policy` lists the policies, how many terms each has and where the running routing applies them. With the name, it shows the terms as they were typed:

```
prisma> show policy
policy-statement UP
  terms: 2
  applied: neighbor 10.0.0.2 import, ospf export
```

`test policy <policy> <prefix>` shows what the policy does with a route for that prefix, term by term, before you apply it anywhere:

```
prisma> test policy UP 198.51.100.0/23
  term 10: no match
      the prefix is in none of its lists or filters
  term 20: reject
      matches
result: reject, by term 20
```

The test starts from the prefix alone. A condition the prefix alone cannot answer - community, as-path, protocol, tag, metric - is not guessed: the term shows up as "decides, if the route has that", and the test continues as if it did not, so you see both paths. When no term decides, the answer states both defaults: accepted on a BGP neighbor, not exported to OSPF or IS-IS.

In configuration mode, `show policy` looks at the candidate, that is, what you are writing and have not yet applied. `test policy` exists only in operational mode and tests the policy in effect: to test a change, `commit` and test right after, or read the candidate with `show policy`.

## Exporting to OSPF and IS-IS

The same policy exports routes into OSPF or IS-IS. Inside `router ospf` or `router isis`, `export <policy>` redistributes what it accepts, and only that:

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list LOOPBACKS
prisma# [edit policy-options / prefix-list LOOPBACKS] 10.255.0.0/16
prisma# [edit policy-options / prefix-list LOOPBACKS] exit
prisma# [edit policy-options] policy-statement PARA-O-OSPF
prisma# [edit policy-options / policy-statement PARA-O-OSPF] term estaticas
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] from protocol static
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] set metric 77
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] then accept
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] top
prisma# [edit] router ospf
prisma# [edit router ospf] export PARA-O-OSPF
```

**In an export to OSPF or IS-IS, a route that no term accepted is not exported**, as in Junos. It is the opposite of BGP, where what nobody decided passes: here, only what the policy deliberately accepted enters the protocol. `from protocol` says where the route came from - `static`, `direct` (the interface networks), `kernel`, `bgp`, or the other interior protocol; a term without `from protocol` applies to any source.

`no export <policy>` undoes it. In IS-IS the export applies to IPv4 and IPv6 and to both levels.

**What exists only in a BGP route is refused in an export to OSPF or IS-IS:** `from community`, `from as-path`, `set local-preference`, `set community`, `set as-path-prepend` and `set next-hop`. A map that silently ignored a term's condition would export exactly what that term was written to hold back.

Measured in a lab with one OSPF adjacency and two static routes: the policy that accepted only one of them, with `set metric 77`, made the neighbor receive that route with cost 77, and only that one. With `redistribute static` instead of the policy, both arrived, with the default cost.

## Route-maps and lists, routing's own form

`policy-options` is the recommended way, but not the only one: routing's route-maps and lists are still accepted, and they are what a neighbor uses in `neighbor <neighbor> route-map <name> in|out` and `neighbor <neighbor> prefix-list <name> in|out`. What `prismash` checks of them, typed at the top of configuration mode:

| Command | What it creates |
|---------|-----------------|
| `ip prefix-list <name> ...` and `ipv6 prefix-list <name> ...` | a prefix list, with `permit`/`deny` and a `ge`/`le` range |
| `bgp as-path access-list <name> ...` | a list of AS-path expressions |
| `bgp community-list <name> ...` and `bgp large-community-list <name> ...` | a list of communities or large communities |
| `route-map <name> permit\|deny <seq>` | a route-map clause; enters it |

Inside a route-map clause:

| Command | Argument | What it does |
|---------|----------|--------------|
| `match ip address prefix-list` | `<list>` | IPv4 routes whose prefix is in the list |
| `match ip next-hop prefix-list` | `<list>` | IPv4 routes whose next hop is in the list |
| `match ipv6 address prefix-list` | `<list>` | IPv6 routes whose prefix is in the list |
| `match rpki` | `valid`, `invalid` or `notfound` | by origin validation state (RPKI section, below) |
| `match as-path` | `<list>` | by AS-path   |
| `match community` and `match large-community` | `<list>` | by communities |
| `match metric` | `<0-4294967295>` | by MED       |
| `match peer` | `<neighbor>` | by the neighbor that sent the route |
| `set local-preference` | `<0-4294967295>` | which path this box prefers; higher wins |
| `set metric` | `<value>`, `+`/`-` value, `rtt`, `+rtt`, `-rtt`, `igp` or `aigp` | the MED sent, replaced or adjusted |
| `set community` and `set large-community` | `<AS:NN> [additive]` | the communities carried; `additive` keeps the ones already there |
| `set next-hop` | `<A.B.C.D>` | the announced next hop |
| `set as-path prepend` and `set as-path exclude` | `<asn> [<asn> ...]` | lengthens the path, or removes ASes from it |
| `set weight` | `<0-4294967295>` | this box's local preference, never announced |
| `set origin` | `igp`, `egp` or `incomplete` | the origin attribute |
| `call`  | `<route-map>` | runs another route-map before deciding |
| `on-match next` or `on-match goto <seq>` |          | what to do when the clause matches |
| `description` | `<text>` | what the clause is for |

What is not in these tables also reaches routing, marked `<- not checked`. The details — clause order, what is refused, the `ge`/`le` range, AS-path expressions — are in [Routing, SSH, users and SNMP](/s/wiki-en/doc/routing-ssh-users-and-snmp-jjxcPssV72).

# What is refused before reaching routing

These are refusals of **form**, not of function: whether the neighbor will answer is not in the configuration, and the box does not pretend to know.

* **neighbor without** `**remote-as**` — FRR refuses any other statement for it; on a session without an address FRR accepts it and leaves the neighbor stuck in Idle with AS 0 forever, which is worse, so the refusal applies to both forms;
* **session without an address pointed at an interface the box does not have** — FRR accepts it, the neighbor stays in Idle and nothing more is said about it;
* **peer group nobody declared** — `neighbor X peer-group IX` without the `neighbor IX peer-group` that creates the group;
* **member whose group has no AS either** — the member inherits the group's AS, so a neighbor without its own AS is correct when the group has one; what cannot happen is neither of them having one;
* `**update-source**` **pointing at an interface the box does not have** — FRR accepts it, the session is sourced from nowhere and never comes up, and the configuration still reads fine.

## A route-map referenced and never defined

```
error: no route-map called 'RM-TRANSITO-IN' is defined, and FRR denies
    everything this statement governs until one is; define it, or write
    'route-map RM-TRANSITO-IN permit 10' to let everything through
    neighbor 200.150.0.2 route-map RM-TRANSITO-IN in
```

This is the refusal that prevents the silent session. Routing **accepts** pointing a neighbor at a route-map that does not exist: the session comes up, stays Established, and carries no routes at all. Three weeks later `show bgp summary` shows a session up with zero prefixes, and the configuration still reads perfectly well — the policy is there, written, pointing at nothing.

The refusal applies to the three forms that were measured denying everything: `neighbor <address> route-map`, `redistribute ... route-map` and `network <prefix> route-map`. The message **names the missing route-map** and gives both ways out: define the policy, or write the clause that lets everything through.


:::info
`call` to a nonexistent route-map is **not** refused, and the difference was measured, not deduced: routing skips a call it cannot resolve and the clause carries on, so the prefix keeps being announced. It is a dead line, not a dangerous one:

```
warning: no route-map called 'RM-COMUNIDADES' is defined; FRR skips a call it
    cannot resolve, so this line does nothing
    call RM-COMUNIDADES
```

The opposite — a defined route-map nobody uses — is also just a warning. Dead configuration is clutter, not danger, and it is exactly what sits on the box between two commits for someone who builds the policy first.

:::


:::info
On a box that is **already** in that state, the statement comes out as a warning and the commit goes through. That is on purpose: the same check runs when the box loads the saved configuration at boot, and there it would refuse the **whole** file — a box whose stored configuration has the reference would come up with nothing. What is refused is the commit that **introduces** the reference; a box that already has it can still be worked on.

:::

# What is warned about and not refused

## An eBGP session without `maximum-prefix`

```
warning: this eBGP session has no 'maximum-prefix': a peer that leaks a full
    table fills this box and nothing here stops it. 'neighbor 200.150.0.2
    maximum-prefix 10000' tears the session down instead of the box
    neighbor 200.150.0.2 remote-as 65001
```

A neighbor that leaks the full table into a box sized for a few thousand routes takes the box down, and BGP **prevents nothing by default**: routing accepts every prefix the neighbor sends until memory runs out. `maximum-prefix` is the only statement that drops the session instead of the box, and until now nothing on this box mentioned that it was missing.

It is **a warning and never a refusal**, and that is the right decision: a route server, a customer whose table is known to be small and a lab link run without a limit on purpose. The choice belongs to the operator.


:::info
The warning **speaks once**: it comes out for the neighbors that **this change** puts in that state. A box with three transits deliberately without a limit would carry three warnings on every commit for the rest of its life, and an alarm that is always on is an alarm nobody reads.

**iBGP** sessions are left out by decision, not by oversight: on the other side is a box from the same operator, the table is the one this AS has already decided to carry, and a limit there fires when the network grows, not when someone leaks. This applies to `remote-as` equal to the stanza's own AS and to `remote-as internal`.

A `maximum-prefix` on the **peer group** covers all its members — it is where someone with six transits sets the limit just once. `maximum-prefix-out`, however, does not count: that statement limits what **this** box announces, which is another matter.

:::

# The password

`neighbor <address> password <password>` is the secret itself, not a hash of it, and it opens the session **on the other router** too. That is why it is not printed back:

```
 neighbor 200.150.0.2 password <set, not shown>
```

This applies both to `show running-config` and to `show this` inside the block. It is display only: the saved configuration and the rollback revisions keep the real value, otherwise a rollback could not restore the session — and that is why those files are not readable by just anyone.

# Origin validation (RPKI)

An ISP that validates origins in the rest of its network should not have, at the edge, a box that accepts whatever it is told. This box's routing speaks RTR: it connects to a validator you already operate and starts marking each learned route as `valid`, `invalid` or `notfound`, so that your policy decides what to do with each one.

```
configure
 rpki
  rpki cache tcp 10.0.0.10 8282 preference 1
  exit
 route-map RM-TRANSITO-IN deny 5
  match rpki invalid
  exit
 route-map RM-TRANSITO-IN permit 10
  exit
 router bgp 65000
  neighbor 200.150.0.2 route-map RM-TRANSITO-IN in
  exit
 commit
```

The first clause discards what the validator rejects; the second lets everything else through. Without the second, the route-map denies everything at the end — which is a policy's normal behavior, and here would filter the whole session instead of filtering only the invalid.

## What the `rpki` block understands

| Command | Argument | What it does |
|---------|----------|--------------|
| `rpki cache tcp` | `<host> <port> [source <A.B.C.D>] preference <1-255>` | a validator, via RTR over TCP |
| `rpki cache ssh` | `<host> <port> <user> <private-key> [known-hosts] [source <A.B.C.D>] preference <1-255>` | a validator, via RTR over SSH |
| `rpki polling_period` | `<1-86400>` | seconds between updates |
| `rpki expire_interval` | `<600-172800>` | seconds until discarding what the validator sent |
| `rpki retry_interval` | `<1-7200>` | seconds until retrying a lost validator |
| `rpki reset` |          | discards what the validators sent and asks again |

## The transport word is not optional

The form is `rpki cache tcp <host> <port> [source <A.B.C.D>] preference <1-255>`, or `rpki cache ssh ...`. Omitting `tcp` is not a shortcut: it is an unknown command, and the box refuses it as it would refuse any word that does not exist.

## It is a module, and a module that is not loaded does not exist

RPKI support comes in a package separate from routing, `frr-rpki-rtrlib`. The appliance already installs it. The symptom when it is missing is misleading and worth recognizing:

```
bgpd is not running
```

Every single RPKI word answers that — because routing refuses to start when told to load a module it cannot find. The answer points to the wrong place: it looks like routing is down, it is a missing module. That is why the boot self-test checks the module **by name**, and not by the routing version, which reports RPKI support whether the module is present or not.

## The two things `commit` warns about

Writing the policy before configuring the cache is a legitimate order of work, so neither of the two is a refusal — but both change what the policy does, and silently.

```
warning: no RPKI cache is configured, so every route is notfound and this
    clause matches all of them
    match rpki notfound
```

Without a cache, everything is `notfound`, and that is what the warning is about: a clause on `notfound` matches **all** of the box's routes. The other side is warned too, with the equivalent sentence — a clause on `valid` or `invalid` matches **none**, because without a validator no route ever becomes one or the other. The same policy, with and without a cache, does opposite things.

## The failure this prevents, and the one it creates


:::warning
A validator that went down ends up marking **everything** as `notfound`. A policy that denies `invalid` keeps working in that state; a policy that treats `notfound` as suspicious starts degrading every route on the box the day the cache goes away — and a cache being down is an ordinary event, not a catastrophe.

:::

That is the reason the recommendation is to deny `invalid` and not to distrust `notfound`. The first fails closed for what the validator rejects; the second fails closed for the entire internet.

## What the box says when the cache disappears

With the cache up, the BGP summary says:

```
  RPKI: 1 of 1 cache(s) connected
```

And when it disappears:

```
  RPKI: 1 cache(s) configured, NONE connected
        validation is running on data the caches already sent, which expires;
        after that every route is notfound
```

Both halves of that second sentence matter. Validation does **not** stop right away: the prefix table the cache already sent stays valid, and only expires after routing's own `expire_interval` — 7200 seconds by default. Saying "nothing is validated" would send you looking for an incident that has not started yet; saying only "cache down" would hide that it is going to start.


:::info
In `show rpki cache-connection`, the header line `**Connected to group 1**` stays there as long as the cache is configured — measured seventy seconds after the validator was killed, it still said *Connected*. What tells the truth is each cache's `**(connected)**` marker, which disappears in under a second. Everything in this product reads the marker; when checking by hand, read the marker too.

:::

# Operating the session: what you type when something is wrong

## `show bgp summary`

```
prisma> show bgp summary
  Neighbour          AS       State          Prefixes  Up
  200.150.0.2        65001    Established    412       03:11:55
  200.150.1.2        65002    Established    398       01:02:37

  2 session(s), 2 established
  all 810 BGP prefix(es) are in the forwarding table
```

The last line is the one that exists only here. An Established session carrying 412 prefixes is a true answer to **another** question: what forwards is the dataplane's table, and a prefix that routing has and the dataplane did not receive moves no packets at all. When the two disagree:

```
  3 of 412 BGP prefix(es) are NOT in the forwarding table
    200.150.9.0/24
    ...
    'show route verbose' compares the two tables in full
```

The count is of the total, not per session. The forwarding table does not record which session the route came through, and putting a per-neighbor number there would be putting a number that does not hold up.

## Routes the box is not keeping

`show bgp neighbor <address> received-routes` shows what the neighbor sent **before** inbound policy — and the box only has that if told to keep it. Without `soft-reconfiguration inbound` the table comes out empty, and "I received no routes" and "I am not keeping them" are different answers to be looking at during an incident:

```
error: 200.150.0.2 does not keep what it received
    so this would print an empty table either way
    add 'neighbor 200.150.0.2 soft-reconfiguration inbound' under the address
    family; it costs memory
    'show bgp neighbor 200.150.0.2 routes' shows what survived inbound policy,
    which the box does have
```

`clear bgp <address> soft in` refuses for the same reason, and the way out it offers is the one that always works: reset the session and take the routes again.

```
error: 200.150.0.2 does not keep what it received
    so there is nothing to re-apply
    add 'neighbor 200.150.0.2 soft-reconfiguration inbound' under the address
    family, which costs memory,
    or use 'clear bgp 200.150.0.2' to reset the session and take the routes
    again
```

Both refusals come in lines within the console width. For a while the verdict and the first explanation came out glued together on a single line, which measured 92 to 106 columns on a 79-column screen.

And note that the verdict **starts with the address**, without the word `neighbour` in front. It was dropped because the address right after it already says who is being talked about, and it cost ten columns on a line the box does not wrap: with an IPv6 neighbor instead of an IPv4 one, the verdict reached 87 columns on a 79-column screen. The neighbor's address goes into every line of this refusal, so it is what governs the width of all of them — and that is why the extra word was the first to go.

## And when the one not answering is routing

There are **three** answers, not two. "The neighbor does not keep it" is a reading of the configuration in effect — and on a box whose routing is not answering there is no configuration read: there is no reading at all. Saying there that `soft-reconfiguration inbound` is missing would send you to add a line the box may already have, for a daemon that is not up to receive it, and would point to a command that asks the same silent routing.

```
error: FRR is not answering
    so whether 200.150.0.2 keeps what it received cannot be read
    an empty table here would be this box's control plane being down rather
    than the neighbour sending nothing: 'systemctl status frr' and its journal
    say why
```

`clear bgp <address> soft in` answers the same way, and adds the part that matters there: `**clear bgp**` **alone would not solve it either**, because what holds the session is precisely the routing that is silent.

```
error: FRR is not answering
    so whether 200.150.0.2 keeps what it received cannot be read
    nothing was re-applied, and 'clear bgp' would reset nothing either - both
    go to the FRR that is silent: 'systemctl status frr' and its journal say
    why
```

The verdict of these two **does not carry the neighbor's address**, and that is deliberate: the address goes on the line below. The first line is not wrapped by anyone, and with an IPv6 neighbor in the middle of it, it would reach 99 columns on a 79-column screen.

An empty table on such a box is **your** box's control plane being down, not the neighbor failing to send routes. They are different problems, and that is what this third answer exists to separate.

## `clear`

```
prisma> clear bgp 200.150.0.2
prisma> clear bgp 200.150.0.2 soft in
```

It is operational: it changes nothing on disk. It requires an administrator account, because resetting a session is a deliberate outage.

## Maintenance without dropping traffic

```
router bgp 65000
 bgp graceful-shutdown
 exit
```

RFC 8326. Before maintenance, traffic **drains** instead of dropping: announcements go out tagged with the graceful shutdown community, and the routes this box received get local preference zero — that is, it stops being the preferred path while whoever has another path takes over.

`no bgp graceful-shutdown` undoes both things. A maintenance switch that does not switch back is not a maintenance switch.


:::info
Check by the effect, not by the text: `show bgp <prefix>` on the neighbor should show the `gracefulShutdown` community, and `show bgp <prefix>` here should show local preference 0. If the configuration has the line and the neighbor does not see the community, the session has not re-announced yet.

:::

# How to check

* `show running-config` shows the BGP stanza as the box reads it back, with the password omitted;
* `show this` inside the block shows only what is at that level;
* `show bgp summary` shows the sessions and how many prefixes each one brought;
* `show route` shows what **forwards** — a route BGP learned that is not there moves no packets at all.

# See also

* [Routing, SSH, users and SNMP](/s/wiki-en/doc/routing-ssh-users-and-snmp-jjxcPssV72) — prefix-lists, policies and a pool's public prefix
* [Routing policy](/s/wiki-en/doc/routing-policy-policy-options-WXZOz2enzp) — everything `policy-options` takes, and where a policy applies
* [commit, rollback and save](/s/wiki-en/doc/commit-rollback-and-save-HVsz5GElE1) — how a change is applied and undone
