# Routing policy (policy-options)

Reference for everything `policy-options` accepts in `prismash`: prefix lists, communities, large and extended communities, AS-path expressions, policies with their terms, where a policy is applied and how to check it before and after - how many routes each term matched, what it would do with a route and which policy decided a route the box has. For those who write BGP filters or decide what goes into OSPF and IS-IS.

The policy is written once, with a name, in Junos format, and applied in as many places as needed. The box translates it into what routing understands at `commit`.

# The levels and the prompts

`policy-options` is a level of configuration mode, and inside it there are two more, one inside the other:

| Level | How to enter | Prompt | What is typed there |
|-------|--------------|--------|---------------------|
| `policy-options` | `policy-options` in `[edit]` | `prisma# [edit policy-options]` | lists, communities, as-paths and policies |
| prefix list | `prefix-list <name>` | `prisma# [edit policy-options / prefix-list <name>]` | one network per line |
| policy | `policy-statement <name>` | `prisma# [edit policy-options / policy-statement <name>]` | the terms           |
| term  | `term <name>` inside the policy | `prisma# [edit ...ptions / policy-statement <name> / term <name>]` | `from`, `set` and `then` |

A prompt longer than 44 characters keeps the **end**, which is where the next line will land; that is why the term prompt starts with `...`. Entering a named level already creates what it names: a freshly created list or term exists before it has anything inside.

At any level, `exit` goes up one level and `top` goes back to `[edit]`. `?` and TAB list what the level accepts.

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list CLIENTES
prisma# [edit policy-options / prefix-list CLIENTES] 203.0.113.0/24
prisma# [edit policy-options / prefix-list CLIENTES] exit
prisma# [edit policy-options] community MARCA members 65000:100
prisma# [edit policy-options] policy-statement SAIDA
prisma# [edit policy-options / policy-statement SAIDA] term 10
```

The same policy, in the form the box saves it and in which you can type or paste it:

```
policy-options
 prefix-list CLIENTES
  203.0.113.0/24
  exit
 community MARCA members 65000:100
 policy-statement SAIDA
  term 10
   from prefix-list CLIENTES
   set as-path-prepend 65000 65000
   then accept
   exit
  term 20
   from community MARCA
   then reject
   exit
  exit
 exit
```

# The `policy-options` level

| Command | Argument | What it does |
|---------|----------|--------------|
| `prefix-list` | `<name>` | creates or edits a list of networks and enters it |
| `community` | `<name> members <asn:value>...` | a named set of communities. Each member is `ASN:value`, both from 0 to 65535. Typing the same name again **replaces** the set |
| `large-community` | `<name> members <asn:value:value>...` | a named set of large communities (RFC 8092). Each part goes from 0 to 4294967295. Typing the same name again replaces the set |
| `extended-community` | `<name> members rt:<asn>:<value>\|soo:<asn>:<value>...` | a named set of extended communities: route target (`rt`) and site of origin (`soo`). The first field is an ASN or an IPv4 address. With an ASN up to 65535 the value goes up to 4294967295; with a larger ASN or with an address, up to 65535 |
| `as-path` | `<name> <expression>` | a named AS-path regular expression. Surrounding quotes are removed. Typing the same name again replaces the expression |
| `policy-statement` | `<name>` | creates or edits a policy and enters it |
| `no`    | `<statement>` | removes a list, a community, a set of large or extended communities, an as-path or a policy, with everything it has inside |

A name, of a list, community, as-path, policy or term, has letters, numbers, `.`, `_` and `-`, starts with a letter or number and has up to 32 characters.

In the AS-path expression, `_` is the routing rule: any boundary (start, end, space or comma), not an underscore. An expression that does not compile is refused immediately.

## Inside `prefix-list <name>`

| Command | Argument | What it does |
|---------|----------|--------------|
| `<network>` | address/length | puts the network in the list. IPv4 and IPv6 can be in the same list. Host bits must be zero: `10.0.0.1/8` is refused |
| `no`    | `<network>` | removes the network from the list |


:::info
**A network in a list matches only itself**, with that exact length. `203.0.113.0/24` in the list does not match `203.0.113.128/25`. To also match the more specifics, use `from route-filter` in the term, just below.

:::

## Inside `policy-statement <name>`

| Command | Argument | What it does |
|---------|----------|--------------|
| `term`  | `<name>` | creates or edits a term and enters it. Terms are evaluated **in the order they were created**, not by name order |
| `default-action` | `accept` or `reject` | what the policy does with a route no term decided, instead of the default of where it is applied. Typing it again replaces it |
| `no`    | `term <name>` | removes the whole term |

# The term: `from`, `set` and `then`

A term has three parts. `from` says what the route must match, `set` says what changes in it, `then` says how evaluation ends. All are optional.

## `from`: what the route must match

| Condition | Argument | Matches when |
|-----------|----------|--------------|
| `from prefix-list` | `<name>` | the route's prefix is in the list, exactly |
| `from route-filter` | `<network> exact` | the prefix is that network, with that length |
| `from route-filter` | `<network> orlonger` | the prefix is that network or a more specific one inside it |
| `from route-filter` | `<network> longer` | the prefix is a more specific one inside it, not itself |
| `from route-filter` | `<network> upto /<n>` | the prefix is inside it, from its length up to `/n`. `n` is between the network length and 32 (or 128) |
| `from community` | `<name>` | the route carries a community from the named set |
| `from as-path` | `<name>` | the route's AS-path matches the named expression |
| `from protocol` | `bgp`, `ospf`, `isis`, `static`, `direct` or `kernel` | the route came from that protocol. `direct` are the networks of the box's own interfaces |
| `from tag` | `<0-4294967295>` | the route carries that tag |
| `from metric` | `<0-4294967295>` | the route has that metric |
| `from neighbor` | `<address>` | the route came from that BGP neighbor |
| `from family` | `inet` or `inet6` | the prefix is IPv4 (`inet`) or IPv6 (`inet6`). Without another prefix condition, it matches every prefix of the family |
| `from origin` | `igp`, `egp` or `incomplete` | the BGP origin attribute |
| `from local-preference` | `<0-4294967295>` | the route has that local preference |
| `from next-hop` | `<address>` | the BGP next hop is that one |
| `from large-community` | `<name>` | the route carries a large community from the set |
| `from extended-community` | `<name>` | the route carries an extended community from the set |
| `from validation-state` | `valid`, `invalid` or `unknown` | the result of RPKI origin validation. `unknown` is a route no ROA covers |
| `from source-vrf` | `<vrf>` or `default` | the route was imported from that VRF, or from the default table. Only in a `vrf-import` policy |
| `from policy` | `<policy>` | calls another policy as a subroutine: if it rejects, the route is rejected; if it accepts, the term continues. In a `then reject` term it is different: the term rejects exactly what the subroutine accepts, and what it rejects moves on to the next term (up to 0.148.12 this case rejected everything the term's other conditions caught). A term calls only one policy |

**Conditions of the same type count as "or"; of different types, as "and".** Two `from prefix-list` match a route that is in either of them; one `from prefix-list` and one `from community` require both. `from prefix-list` and `from route-filter` are of the same type: both are about the prefix.

`from tag` and `from metric` take a single value: a second one, in the same term, replaces the first.

`from neighbor`, `from origin`, `from local-preference`, `from next-hop`, `from validation-state` and `from source-vrf` accept several values, which count as "or". Each combination of them becomes one rule in routing, and a term can have at most 10 combinations: more than that is refused at `commit`, and the way out is to split the term in two.

`from validation-state` only makes sense with an RPKI cache configured. Without a cache, every route is `unknown`, and `commit` warns.

**A term without** `**from**` **matches any route.** That is how "everything else" is written at the end of a policy.

## `set`: what changes in the route

| Action | Argument | What it does |
|--------|----------|--------------|
| `set local-preference` | `<0-4294967295>`, `+<n>` or `-<n>` | the BGP local preference, or added to or subtracted from the one the route has |
| `set community` | `add <name>` | adds the set's communities to those the route already has |
| `set community` | `set <name>` | replaces the route's communities with the set's |
| `set community` | `delete <name>` | removes the set's communities from the route |
| `set large-community` | `add`, `set` or `delete` `<name>` | adds, replaces or removes the set's large communities |
| `set extended-community` | `add` or `set` `<name>` | adds or replaces the set's extended communities. There is no `delete`: routing does not know how to remove extended communities by list |
| `set as-path-prepend` | `<asn>...` | prepends those ASNs, in the order typed |
| `set as-path-prepend` | `last-as <1-10>` | repeats the first AS of the path (the neighbor's) that many times |
| `set as-path` | `remove-private` | removes every private AS (64512 to 65534 and 4200000000 to 4294967294) from the AS-path, wherever it is, including the neighbor's own |
| `set metric` | `<0-4294967295>` | the metric: the MED in BGP, the cost in OSPF and IS-IS |
| `set tag` | `<0-4294967295>` | the route's tag |
| `set next-hop` | `<address>` | the next hop in BGP, IPv4 or IPv6 |
| `set next-hop` | `self`   | in an `export`, advertises this box as the next hop |
| `set next-hop` | `peer-address` | in an `import`, uses the neighbor's address as the next hop |
| `set next-hop` | `discard` | discards traffic to the prefix: this is remotely triggered blackhole (RTBH). The next hop becomes 192.0.2.1 (or 100::1 in IPv6), which the box resolves to a blackhole. On a directly connected eBGP neighbor, the neighbor needs `neighbor <neighbor> disable-connected-check`: without it routing does not resolve that next hop, the route is not installed and traffic follows the less specific route. `commit` refuses and says which line is missing |
| `set med` | `<0-4294967295>`, `+<n>`, `-<n>` or `igp` | the BGP MED; `igp` uses the IGP metric to the next hop |
| `set weight` | `<0-65535>` | the weight, which only applies on this box and weighs before local preference |
| `set origin` | `igp`, `egp` or `incomplete` | the origin attribute |
| `set metric-type` | `1` or `2` | the external route type in OSPF. Only in an `export` to OSPF or in OSPF's `default-information` |
| `set src` | `<address>` | the source address the installed route carries in Linux, for what the box itself sends through it. Only in a `fib-policy`. A term with an IPv4 `set src` applies only to IPv4 routes, as if it had `from family inet` (and the same for IPv6) |

The `set` actions are only applied when the term matches.

## `then`: how the term ends

| Command | What it does |
|---------|--------------|
| `then accept` | accepts the route, with the term's `set` actions, and **stops** evaluation |
| `then reject` | rejects the route and stops evaluation |
| `then next term` | applies the `set` actions and moves on to the next term |
| `then next policy` | applies the `set` actions and passes the route to the next policy in the chain. In a policy on its own, the default of where it is applied holds |
| no `then` | the same as `then next term` |

A term has only one `then`: typing another replaces the previous one.

Changing the route is always `set`. Unlike Junos, `then` takes no action: `then local-preference 200` is refused, and the right form is `set local-preference 200` with `then accept`.

## `no` inside the term

| Command | What it does |
|---------|--------------|
| `no from <condition>` | removes that condition |
| `no set <action>` | removes that action |
| `no then <how>` | removes the `then`, and the term goes back to moving on to the next |

`no` takes the whole statement, as it was typed: `no from prefix-list CLIENTES`, not `no from`.

# The route no term decided

The default depends on where the policy is used, as in Junos:

| Where the policy is applied | Route no term accepted or rejected |
|-----------------------------|------------------------------------|
| `import` or `export` on a BGP neighbor | **accepted**                       |
| `export` to OSPF or IS-IS   | **not exported**                   |
| `network ... policy`, `aggregate-address ... policy`, `table-policy` | **accepted**: advertised, or installed |
| `fib-policy`                | **installed**                      |
| `default-originate policy`, `default-information originate ... policy` | **does not count**: without a term that accepts, the default is not originated |
| `suppress-policy`           | **not suppressed**                 |
| the two `advertise` policies | **out**: not advertised and does not count as present |
| `vrf-import ... policy`     | **not imported**: the VRF only receives what a term accepted |

To reject the rest in BGP, use `default-action reject` on the policy, or end with a term without `from` and with `then reject`. To export the rest to an IGP, `default-action accept`, or a term without `from` and with `then accept`.

In a chain, a route no term of one policy decided moves on to the next; a policy's `default-action`, when present, decides right there.

# Where a policy is applied

## On a BGP neighbor: `import` and `export`

Inside `router bgp`, in the address family, per neighbor or per peer group:

```
router bgp 65000
 address-family ipv4 unicast
  neighbor 10.0.0.2 import DO-TRANSITO
  neighbor 10.0.0.2 export SAIDA
 exit-address-family
 exit
```

| Command | What it does |
|---------|--------------|
| `neighbor <neighbor> import <policy>` | the policy filters and alters what arrives from that neighbor |
| `neighbor <neighbor> export <policy>` | the policy filters and alters what is advertised to it |

### A policy chain

Instead of one policy, a list in brackets: `neighbor 10.0.0.2 import [ BOGONS CLIENTES ]`. The route goes through the policies in order; the first that accepts or rejects decides, and `then next policy` passes the route on with what the term changed. The same applies to `export [ ... ]` in OSPF and IS-IS. A policy cannot appear twice in the same chain.

The policy must exist: `import` or `export` of a name no `policy-statement` has is refused at the prompt. On a BGP neighbor the routes are BGP routes, so a term with `from protocol` that does not include `bgp` does not apply there.

In the running configuration the neighbor appears with the `route-map` the policy was translated into: `neighbor 10.0.0.2 route-map PO-SAIDA-OUT out`, or `PO-BOGONS+CLIENTES-IN` for a chain. Every name starting with `PO-` comes from this translation, and a `route-map` or list typed by hand with that prefix is refused.

## In an IGP: `export <policy>`

Inside `router ospf` or `router isis <name>`, `export <policy>` redistributes into the protocol what the policy accepts, and only that:

```
prisma# [edit] router ospf
prisma# [edit router ospf] export PARA-O-OSPF
```

| Command | Where | What it does |
|---------|-------|--------------|
| `export <policy>` | `router ospf` | redistributes into OSPF what the policy accepts, coming from `bgp`, `direct`, `kernel`, `static` or `isis` |
| `export <policy>` | `router isis <name>` | the same for IS-IS, coming from `bgp`, `direct`, `kernel`, `static` or `ospf`, in IPv4 and IPv6 and at both levels |
| `no export <policy>` | in both | undoes the export |

`from protocol` is what chooses where the route came from; a term without `from protocol` applies to any origin. The export covers every origin from the start, so a term added later applies without typing `export` again.

`router ospf6` has no `export`.

## In BGP, beyond the neighbor

Inside `router bgp`, in the address family:

| Command | What it does |
|---------|--------------|
| `neighbor <neighbor> default-originate policy <policy>` | advertises the default route to the neighbor as long as there is a route in the BGP table that the policy accepts. The policy's `set` actions go on the default |
| `network <prefix> policy <policy>` | originates the prefix with the policy's `set` actions; if it rejects, the prefix is not advertised |
| `aggregate-address <prefix> [summary-only] [policy <policy>] [suppress-policy <policy>]` | originates the aggregate. `policy` gives the aggregate's `set` actions; `suppress-policy` holds back the more specific routes it accepts and lets the others through. `summary-only` holds back all of them, and that is why it does not go together with `suppress-policy` |
| `neighbor <neighbor> advertise <policy> exist <condition>` | advertises what the first policy accepts only while there is a route in the table that the condition accepts |
| `neighbor <neighbor> advertise <policy> non-exist <condition>` | the opposite: advertises only while there is **not**. This is the backup advertisement |
| `table-policy <policy>` | decides which of BGP's best routes go into the routing table and into forwarding. A rejected route stays in BGP and keeps being advertised; it is just not installed. Accepts `set metric` and `set next-hop` |

BGP checks the `advertise` condition every 60 seconds. For less, `bgp conditional-advertisement timer <seconds>` in `router bgp`.

## In the routing table: `fib-policy`

In `[edit]`, one policy per protocol decides what goes into the forwarding table, after the protocol has chosen the route:

```
prisma# [edit] fib-policy ospf SO-O-QUE-SERVE
```

| Command | What it does |
|---------|--------------|
| `fib-policy bgp\|ospf\|isis\|static <policy>` | a route the policy rejects is not installed: the protocol keeps it, but traffic does not follow it. `set src <address>` gives the installed route its source address |
| `no fib-policy <protocol> <policy>` | undoes it    |

That is how you filter what OSPF and IS-IS install: routing has no inbound filter inside those protocols. It applies to IPv4 and IPv6. A `fib-policy` only accepts `set src`, and nothing that only BGP has.

## The OSPF and IS-IS default

| Command | Where | What it does |
|---------|-------|--------------|
| `default-information originate [always] [metric <n>] [metric-type 1\|2] policy <policy>` | `router ospf` | without `always`, originates the default as long as there is a route the policy accepts, among those OSPF redistributes. With `always`, always, with the policy's `set` actions |
| `default-information originate ipv4\|ipv6 level-1\|level-2 [always] [metric <n>] policy <policy>` | `router isis <name>` | the policy is applied to the default route itself: accepted, the default is originated with its `set` actions; rejected, it is not |

Here the protocol chooses the routes it looks at, and `from protocol` is refused.

## Between VRFs: `vrf-import`

Inside `router bgp <asn> vrf <vrf>` (or `router bgp <asn>`, which is the default table), in the address family, the VRF imports the BGP routes of another VRF, or of the default table, through the policy:

```
router bgp 65000 vrf red
 address-family ipv4 unicast
  vrf-import svc policy LEAK
  vrf-import default policy LEAK
 exit-address-family
 exit
```

| Command | What it does |
|---------|--------------|
| `vrf-import <vrf>\|default policy <policy>` | the VRF receives the routes the policy accepts, with its `set` actions. What no term accepted stays out |
| `vrf-import <vrf>\|default` | imports everything, without a policy, in a family that has no policy yet |
| `no vrf-import <vrf>\|default` | undoes it; the policy goes away along with the family's last import |

**One policy per address family.** Routing applies the same policy to every VRF a family imports, so a second policy in the same family is refused, and so is an import without a policy in a family that already has one. To treat each origin differently, use `from source-vrf` in the terms. It applies to IPv4 and IPv6, each family with its own.

In an import the policy accepts `set local-preference`, `set community`, `set large-community`, `set med`, `set metric`, `set origin` and `set weight`. The next hop is the source VRF's, and that is the way traffic goes: `set next-hop` is refused, and so is `set extended-community`, because route targets are what routing itself uses to import. `from neighbor` is refused: the imported route comes from a VRF, not from a neighbor, and what says which VRF is `from source-vrf`.

There is no `rd` or `route-target` to configure: routing chooses them on its own for this kind of import.

**The Linux VRF comes from** `**vrf <name> table <n>**`**.** The routing VRF needs a Linux VRF with the same name; declare it with `vrf red table 10`, in the same table as the `vrf 10` of its interfaces (see [Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn)).

**A VRF's connected subnet also crosses over.** When the source VRF has `redistribute connected` and the other one imports it, routing installs that route in Linux pointing at the source VRF itself, with no next hop. Since 0.149.0 the box carries that route into the dataplane on its own: the importing VRF looks up the destination in the source VRF's table. There is nothing to configure; each route carried appears once in the journal, with the names of the VRFs:

```
prisma-routing-leaks: 10.10.0.0/24 in svc, through red (VPP table 30, a lookup in table 10): installed
```

It goes away on its own when the import is undone, and comes back on its own after a dataplane restart. Up to 0.148.16 the route existed in BGP and in Linux, but not in the dataplane, and a host on the connected subnet was not reached from the other VRF.

`prisma-bp`, and any VRF whose name starts with `prisma-`, belongs to the box itself: no route enters or leaves it, neither through `vrf-import` nor through routing's `import vrf` typed by hand.

A static route can carry a tag: `<prefix> next-hop <destination> tag <n>` in `router static`, and `from tag <n>` matches it. It is the way to mark a static route for an `export` or for a condition.

# Checking: `show policy` and `test policy`

| Command | Mode | What it shows |
|---------|------|---------------|
| `show policy` | operational and configuration | each policy, how many terms it has and where the running routing applies it |
| `show policy <policy>` | operational and configuration | the same thing, with the terms as they were typed and, at each place where it is applied, how many routes each term matched |
| `test policy <policy> <prefix>` | operational | what the policy does with a route to that prefix, term by term |
| `test policy <policy> route <prefix> ... [at <use>]` | operational | the same for a route you describe - communities, AS-path, neighbor, MED and the rest -, at the place where the policy is used, with the route after the `set` actions |
| `test policy <policy> rib <prefix> ... [at <use>]` | operational | the same for the route the box has |
| `show policy route <prefix> [vrf <name>]` | operational and configuration | which policy decided a route the box has, at each place it went through, beside what routing did |

In operational mode both read the applied policy. In configuration mode, `show policy` reads the candidate, what you are writing and have not yet applied.

```
prisma> show policy
policy-statement SAIDA
  terms: 2
  applied: neighbor 10.0.0.2 export, ospf export
```

The other places appear the same way: `neighbor 10.0.0.2 default-originate`, `network 10.40.0.0/24`, `aggregate-address 10.30.0.0/16 policy` or `suppress-policy`, `neighbor 10.0.0.2 advertise` and, for the condition, `neighbor 10.0.0.2 advertise non-exist`, `table-policy`, `fib-policy static`, `ospf default-information`, `vrf-import`.

A policy that was written and is not yet used anywhere says `applied: nowhere in the running routing`. A policy in a chain appears with the whole chain: `neighbor 10.0.0.2 import [ BOGONS CLIENTES ]`. The `default-action`, when present, appears before the terms.

`test policy` goes through the terms in order and stops at the one that decides:

```
prisma> test policy SAIDA 203.0.113.0/24
  term 10: accept (set as-path-prepend 65000 65000)
      matches
result: accept (set as-path-prepend 65000 65000), by term 10
```

The test starts from the prefix alone. `from family` it answers from the prefix. A condition the prefix alone does not answer (community, large or extended community, as-path, protocol, tag, metric, neighbor, origin, local preference, next hop, RPKI validation, source VRF, called policy) is not guessed: the term appears as "decides, if the route has that", and the test continues as if it did not, so you can see both paths. A term with `then next policy` ends the test with `result: next policy, by term <term>`. A policy with `default-action` ends with `result: reject, by the policy's default-action` (or `accept`). When no term decides and there is no `default-action`, the answer gives both defaults:

```
prisma> test policy SAIDA 198.51.100.0/24
  term 10: no match
      the prefix is in none of its lists or filters
  term 20: if the route's community match: reject
      the prefix alone does not say
result: no term decided it
    on a BGP neighbor that is accepted; exported into OSPF or IS-IS it is not
    as a network, aggregate-address, table or FIB policy it is accepted; as the
    routes an origination, a suppression or a conditional advertisement waits
    for, it is not
    imported into a VRF, by vrf-import, it is not
```

# What is refused

Everything below is refused **at the prompt**, and nothing is stored in the candidate.

| You typed | The box says |
|-----------|--------------|
| a name with a character outside what is allowed or longer than 32 | `prefix-list '<name>': not a name` (or whatever the type is), and below it `a name is letters, digits, '.', '_' and '-', at most 32 characters`; the prompt stays at the level it was at |
| `10.0.0.1/8` in a list or in a `route-filter` | `'10.0.0.1/8' is not a network (address/length, host bits zero)` |
| `community C members 70000:1` | `'70000:1' is not a community: ASN:value, each 0-65535` |
| an as-path expression that does not compile | `the expression does not compile`, with the reason |
| `from route-filter 10.0.0.0/8 between` | `route-filter: exact, orlonger, longer or upto /<length>` |
| `from route-filter 10.0.0.0/8 upto /4` | `the length is between 8 and 32` |
| `from protocol rip` | `from protocol: bgp, ospf, isis, static, direct or kernel` |
| `from metric 99999999999` | `from metric <0-4294967295>` |
| a condition that does not exist | `from:` followed by the list of conditions |
| `large-community L members 1:2` | `'1:2' is not a large community: ASN:value:value, each 0-4294967295` |
| `extended-community E members xx:1:2` | `'xx:1:2' is not an extended community: rt:<asn>:<value> or soo:<asn>:<value>, or an address in place of the ASN` |
| `from family ipx`, `from origin bgp`, `from validation-state good` | the list of what the condition accepts |
| `set extended-community delete E` | `set extended-community add\|set <name>`: routing does not remove extended communities by list |
| `then next hop` | `then: accept, reject, next term or next policy; a change to the route is 'set ...'` |
| a second `from policy` in the same term | `a term calls one policy` |
| `neighbor ... import [ A A ]` or two names without brackets | `usage: neighbor <peer> import\|export <policy> \| [ <policy>... ]` |
| `set next-hop self` in a policy applied as `import`, `set next-hop peer-address` as `export`, `set metric-type` outside an `export` to OSPF | `this policy cannot be applied as import; nothing staged` (or `export`), with one line per offending term |
| an action that does not exist | `set:` followed by the list of actions |
| `set src` in a policy applied outside a `fib-policy` | `cannot be applied on network; nothing staged` (or wherever it is), and below it `set src is for a fib-policy` |
| in a `fib-policy`, a `set` that is not `src`; in a `table-policy`, one that is neither `metric` nor `next-hop`; in a `suppress-policy` or in an `advertise`, any `set` | `set <action> does nothing on <where>`, with what that place accepts |
| `aggregate-address ... summary-only suppress-policy ...` | `summary-only suppresses every more specific route, and suppress-policy the ones it accepts: one or the other` |
| `fib-policy connected ...`, or one of the new commands typed halfway | `not how this statement is typed; nothing staged`, and below it the right form |
| `from protocol` in a `default-information` policy | `from protocol has nothing to match on ospf default-information` (or `isis`) |
| `set next-hop abc` | `set next-hop: 'abc' is not an address` |
| `then` with anything other than `accept`, `reject` or `next term` | `then: accept, reject or next term; a change to the route is 'set ...'` |
| `no` of something that does not exist | `policy-options has no such statement; nothing removed`, with the statement |
| `neighbor ... import` or `export` of a policy that does not exist, or `export` in an IGP | `no policy-statement named <name>` |
| a `route-map` or list with a name starting with `PO-` | `the PO- names belong to policy-options; nothing staged` |
| at `commit`, a term that cites a `prefix-list`, `community`, `large-community`, `extended-community`, `as-path` or policy that does not exist | `policy-statement <policy> term <term>: no prefix-list <list>` (or whatever the type is); nothing is applied |
| at `commit`, `set next-hop discard` in the `import` of a directly connected eBGP neighbor without `disable-connected-check` | `set next-hop discard is not resolved on a single-hop eBGP neighbor: add 'neighbor <neighbor> disable-connected-check', or the route is never installed and its traffic is forwarded` |
| at `commit`, policies that call each other in a loop with `from policy` | `policy-statement <policy>: from policy reaches itself again` |
| at `commit`, a `then reject` term with `from policy X`, when X has a term with `from policy` that is not `then accept` | `policy-statement <policy> term <term>: then reject with from policy X, and ...` |
| at `commit`, two things the compiler would name the same in FRR: a policy called `X-SUB` or `X-NOT` beside the map that `from policy X` calls, a term `1-Ta` of policy `X` beside term `a` of policy `X-T1`, a deleted community with the same name as a term's list | `policy-statement <policy>: <one> and <other> compile to the same FRR route-map <name>; FRR keeps one object per name, so rename one of them` (or whichever list it is) |
| `vrf-import prisma-bp ...`, or a `vrf-import` inside `router bgp <asn> vrf prisma-bp` | `prisma-bp is a VRF of the box's own: no route leaks out of it` (or `into it`) |
| `vrf-import red` inside `router bgp <asn> vrf red` | `a VRF does not import from itself (red)` |
| `vrf-import` outside `address-family ipv4 unicast` or `ipv6 unicast` | `a VRF imports per address family` |
| `vrf-import <vrf> policy B` in a family that already imports through policy A, or `vrf-import <vrf>` without a policy | `this address family imports through policy A`, and below it the right form |
| `set next-hop` or `set extended-community` in a `vrf-import` policy; `from neighbor` in it | `set next-hop does nothing on vrf-import` (or whichever action it is), with what it accepts; `from neighbor matches nothing on vrf-import` |
| `no vrf-import <vrf>` of an import the family does not have | `nothing removed`, and below it `this address family does not import <vrf>` |
| `from source-vrf` in a policy applied outside a `vrf-import` | `from source-vrf is for a vrf-import, where a route is leaked` |
| at `commit`, a term with more than 10 combinations | `more than 10 combinations of neighbor, origin, local-preference, next-hop, validation-state and source-vrf; split it into terms` |

## What exists only in BGP, exported into an IGP

An OSPF or IS-IS route has no community, AS-path, local preference or BGP next hop. A term that talks about those things is refused at the time of the `export`: the box answers `this policy cannot be exported into isis; nothing staged` (or `ospf`) and, below it, one line per offending term, giving the term and what in it `is for BGP routes`.

What counts as BGP-only: `from community`, `from as-path`, `from neighbor`, `from origin`, `from local-preference`, `from next-hop`, `from large-community`, `from extended-community`, `from validation-state`, `from source-vrf`, `set local-preference`, `set community`, `set large-community`, `set extended-community`, `set as-path-prepend`, `set as-path`, `set med`, `set weight`, `set origin` and `set next-hop`. It also applies to the policies the exported one calls with `from policy` or that come after it in the chain. The refusal exists because silently ignoring the condition would export exactly what the term was written to hold back. If the same logic serves both, write two policies: one for the BGP neighbor, another for the IGP.


:::warning
**Check with** `**show policy <policy>**` **and** `**test policy**` **before** `**commit**`**.** A policy applied on a neighbor decides what the session carries: a term that does not do what you thought drops or leaks prefixes on the whole session. A term can be typed before the list it cites, but `commit` refuses a term that cites a list that does not exist. A `prefix-list` that exists and is **empty** matches no route: a term that depends only on it catches nothing. And remember that, on a BGP neighbor, what no term decides is **accepted**; to advertise only what the lists say, end the policy with a term that rejects the rest.

:::

## How many routes each term matched

`show policy <policy>` ends with the counters: at each place where the policy is applied, how many routes each term matched. They come from routing itself - the process that runs that use (`bgpd` in BGP, `ospfd` and `isisd` in the export to the IGP, `zebra` in the `fib-policy`) counts how many times each clause matched - and the box maps each clause back to the term, and to the policy within the chain, it came from:

```
prisma> show policy UP
policy-statement UP
  terms: 3
  ...
  applied: neighbor 10.0.0.2 import [ UP TAIL ]
  hits, the routes each term matched, by FRR's counters:
    neighbor 10.0.0.2 import
      bgpd, route-map PO-UP+TAIL-IN
              1  UP term cust
              1  UP term black
              1  UP term bogon
              1  TAIL term t
              1  no term decided: the use's default, accept
  a hit is a clause that matched a route, each time the daemon ran the map: a
  route evaluated again - a soft reset, a change - counts again. One route-map
  serves every place it is applied, and they share its counts.
```

Three things to read correctly:

* **It counts matches, not routes.** A route evaluated again - a soft reset, an update from the neighbor - counts again. The number rises with the routes, but it is not the table size.
* **Two places that apply the same policy share the counter.** Two neighbors with `import UP` are a single count; the list of places appears above it.
* `**-**` **and** `**no counter**` **are not zero.** `-` is a term that does not exist in that use (a `from protocol` of another protocol, for example). `no counter` is a process that did not answer for that policy - it is not running, or does not have it.

## Testing with a described route: `test policy ... route`

Instead of just the prefix, describe the route and say where the policy is: `neighbor`, `community`, `large-community`, `extended-community`, `as-path`, `origin`, `local-preference`, `med`, `tag`, `next-hop`, `validation-state`, `source-vrf` and `weight`. Lists are separated by commas, with no space: `community 65001:10,65001:20`, `as-path 65001,64512`. A chain is tested with the policy names joined by `+`: `UP+TAIL`.

```
prisma> test policy UP+TAIL route 203.0.113.0/24 neighbor 10.0.0.2 community 65001:10 as-path 65001
at import: UP+TAIL
  bgpd, route-map PO-UP+TAIL-IN
  the route: as described: a list not given is none, a value not given is not
  known
    prefix 203.0.113.0/24
    neighbor 10.0.0.2
    community 65001:10
    large-community (none)
    extended-community (none)
    as-path 65001
  UP term cust: match
      then accept
result: accept, by UP term cust
  the route after the sets:
    prefix 203.0.113.0/24
    neighbor 10.0.0.2
    community 65001:10 65000:538
    large-community (none)
    extended-community (none)
    as-path 65001
    local-preference 200
```

`at` says in which use: `import` (the default), `export`, `default-originate`, `network`, `aggregate`, `suppress`, `advertise`, `condition`, `table`, `vrf-import`, `ospf-default`, `isis-default`, `ospf <protocol>`, `isis <protocol>` and `fib <protocol>`. That changes the answer: what happens to a route no term decided, what a `from protocol` matches, and which `set` actions apply there - a `set` that use ignores appears as "changes nothing here".

The simulation follows routing's order and rules: the chain, `then next policy`, `default-action`, `from policy` (whose rejection rejects the route; in a `then reject` term, what it accepts is rejected and what it rejects moves on). A list you did not describe is empty; a value you did not give is **unknown**, and the term that needs it is not guessed: the answer says it assumed it did not match, and that it holds only if it does not match.

**RPKI validation without a cache.** Without a synchronized RPKI cache, routing matches **no** validation state - not even `unknown`. The simulation says so when the route's state is not known, and a route read from the box without an RPKI state matches no `from validation-state`, as in routing.

## Testing with a route the box has: `test policy ... rib`

`test policy <policy> rib <prefix> [vrf <name>] [neighbor <address>] [at <use>]` reads the route from the box instead of you describing it: the route of the use's protocol in the routing table for an export to an IGP and a `fib-policy`, and BGP's best path for the rest. In `import`, it is the path that came from the `neighbor` (or the best path's); in `export`, the `neighbor` is who the route goes to; in `vrf-import`, the `vrf` is the VRF the route comes **from**.

What the box stores of an imported route has already gone through the policy. To test the `import`, the box undoes what can be undone and states the rest:

* what the applied policy can change is taken from the copy the neighbor sent, when it exists - and it only exists with `neighbor <address> soft-reconfiguration inbound`;
* communities the policy only adds are removed;
* what cannot be known stays unknown, and the answer says which attribute;
* a route the `import` rejected can only be tested with `soft-reconfiguration inbound`, and without its communities, which that copy does not keep.

## Which policy decided this route: `show policy route`

Routing does not store which term decided each route - only how many routes each term matched. `show policy route <prefix>` answers with what can be known: at each place the route went through, the term that decided, **simulated** as in `test policy ... rib`, and beside it what routing actually did - the path in the BGP table, the advertisement to the neighbor, the route in the routing table and in forwarding, OSPF's external LSA:

```
prisma> show policy route 203.0.113.0/24
route 203.0.113.0/24
  neighbor 10.0.0.2 import: UP+TAIL
    simulated: accept, by UP term cust
    FRR: the path from 10.0.0.2 is in the BGP table: accepted
  table-policy: TBL
    simulated: accept, by no term: the default of the use
    FRR: zebra holds it from BGP
  fib-policy bgp: FIB
    simulated: accept, by term c
    FRR: zebra installed it
  ospf export, from bgp: OEXP
    simulated: accept, by term c
    FRR: ospfd originates it, as external
  neighbor 10.2.0.2 export: DN
    simulated: accept, by term mine
    FRR: advertised to 10.2.0.2
```

If the simulation and routing disagree, the line says so; if the simulation had to assume something it did not know, it also says that routing's answer is the one that holds. A route the neighbor sent with the box's own AS in the path is discarded by BGP before any policy, and appears that way, not as a policy rejection. The origination of the default, conditional advertisement and the suppression of an aggregate are not about a route; the counters of `show policy <policy>` say what they matched.

# See also

* [BGP configuration](/s/wiki-en/doc/bgp-configuration-ykey7SaE44) — neighbors, peer groups and address families, where `import` and `export` are applied
* [Routing, SSH, users and SNMP](/s/wiki-en/doc/routing-ssh-users-and-snmp-jjxcPssV72) — OSPF and IS-IS, and route-maps and lists written in routing's own form
* [commit, rollback and save](/s/wiki-en/doc/commit-rollback-and-save-HVsz5GElE1) — how the policy goes to routing and how to undo it
* [show commands](/s/wiki-en/doc/show-commands-3WvebaLZkm) — `show policy` alongside the others
