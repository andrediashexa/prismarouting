# CGNAT configuration

CGNAT configuration lives inside a hierarchy: you enter `nat`, then `cgnat`, and that is where pools, logging and timeouts live. The side of each interface (`inside` or `outside`) lives in the interface's own block. This page says what exists at each level and how you move between them.

```
prisma> configure
prisma# [edit] nat
prisma# [edit nat] cgnat
prisma# [edit nat / cgnat] pool residencial
prisma# [edit nat / cgnat / pool residencial] prefix-inside 100.64.0.0/22
prisma# [edit nat / cgnat / pool residencial] exit
prisma# [edit nat / cgnat] top
prisma# [edit] interface ens18
prisma# [edit interface ens18] cgnat inside
```

The prompt always says which level you are at. `?` and TAB offer only what is valid at that level: under `nat` you see `cgnat`; under `cgnat` you see `pool`, `interface`, `logging`, `timeout` and the rest of this page.

## Moving between levels

| Command | Where you end up |
|---------|------------------|
| `exit`  | one level up: from the pool to `cgnat`, from `cgnat` to `nat`, from `nat` to the top |
| `up`    | one level up, same as `exit` |
| `up <n>` | `n` levels up at once |
| `top` or `end` | the top of configuration mode, from any level |

`commit` (with all its variants) and `show` work at any level, including inside a pool. You do not need to leave to apply, and after `commit` you stay at the level you were at. `rollback` works under `nat` and `cgnat`; inside a pool, `exit` first. `ping`, `traceroute` and `mtr` also answer at any level.

Under `nat` there are two levels: `cgnat`, for pools with IPv4 inside, and `nat64`, for NAT64 pools (IPv6 inside). Anything else is refused:

```
prisma# [edit nat] pool x
error: under 'nat' there is 'cgnat' and 'nat64'
    each enters its level; 'exit' leaves 'nat'
```


:::warning
**The one-line form no longer exists.** `nat cgnat pool residencial`, `nat cgnat interface ens18 inside` and the older spelling `cgnat pool ...` are refused at the prompt and also in a saved file:

```
prisma# [edit] nat cgnat pool residencial
error: the one-line CGNAT form is gone
    enter 'nat', then 'cgnat', and type the rest there: 'pool <name>',
    'interface <name> inside', 'logging ...'
```

A box whose saved file still has this form refuses the whole file at boot. After upgrading, and **before restarting**, run `save`: it writes what is running already in the new form. [Taking the box to a new version](/s/wiki-en/doc/taking-the-box-to-a-new-version-spJOdnrvOD) explains.

:::

## What lives under `cgnat`

| Command | Argument | What it does |
|---------|----------|--------------|
| `pool <name>` | `<word>` | creates or edits a pool (enters the pool) |
| `interface <if> inside\|outside` |          | enables CGNAT on an interface, on the inside or outside |
| `block-holddown <s>` | `<1-4294967295>` | seconds a block stays reserved after going idle |
| `session-table buckets <n> memory <MB>` |          | how many buckets the session table has; the `memory` number sizes nothing. Valid only before the first pool: with the table already created, the dataplane refuses |
| `memory` | `<GB>`   | the memory given to CGNAT sessions, in whole GB; takes effect from the next reboot, and `show cgnat memory` says whether it is already in effect |
| `timeout <type> <seconds>` |          | global timeouts, in the table below |
| `logging ...` |          | export of block events (IPFIX or syslog), in the table below |
| `identity ...` |          | where the box reads who was at a private address, to put in the record; table below |
| `no pool <name>` |          | removes a pool |
| `no interface <name>` |          | disables CGNAT on the interface (the old form; today it is `no cgnat` in the interface block) |

The fields inside a pool are on the [Pool configuration](/s/wiki-en/doc/pool-configuration-wkJ1DcmG2Y) page.

## Global timeouts

`timeout <type> <seconds>`, under `cgnat`:

| Type | What expires |
|------|--------------|
| `udp` | UDP sessions |
| `icmp` | ICMP sessions |
| `tcp-transitory` | transitory TCP |
| `tcp-established` | established TCP |

A pool can override these values (see the pool configuration page). A pool without its own timeout inherits the global one.

A timeout name that is not one of the four is refused at the prompt, with the list of those that exist. A value below the floor that the RFC for that protocol sets is **applied** and warned about at `commit`: the decision is yours, and the warning exists so that it is made knowingly. The floors, and what breaks below each one, are in [Timeouts and the RFC floors](/s/wiki-en/doc/timeouts-and-the-rfc-floors-HmiUW10Fkh).

## IPFIX logging

Under `cgnat`:

| Command | What it does |
|---------|--------------|
| `logging udp <collector> src <source> [port <n>]` | where IPFIX export over UDP goes |
| `logging enable` | enables export over UDP |
| `logging disable` | disables export over UDP |
| `logging udp disable` | clears the UDP destination |
| `logging tcp <collector> [port <n>]` | exports IPFIX over TCP |
| `logging tcp disable` | stops export over TCP |
| `logging syslog <collector> enterprise <n> [port <n>] [facility <0-23>] [severity <0-7>]` | exports the same events as RFC 5424 syslog lines, over TCP |
| `logging syslog disable` | stops syslog export |

`src` is not optional, and the destination has to exist before `enable`: without it, `commit check` refuses and nothing is applied. Without `port`, IPFIX goes to 4739 and syslog to 601.

TCP exists because the question that matters is not "did the collector receive it?", it is **"what happens when the collector goes down?"**. With UDP, the answer is silence. Over TCP — IPFIX or syslog — what the collector did not receive stays stored on the box and is resent, in order, when it comes back.

Syslog exists for the collector that does not speak IPFIX, which is most of them. `enterprise` is mandatory and is **your** IANA private enterprise number: RFC 5424 reserves names without that suffix for what IANA itself has registered, and every line the box writes carries it. Without it `commit` refuses the line.

UDP, TCP and syslog are independent: you can export over more than one at the same time.

## Who was at the address: `identity`

A court order names a person; the record names a private address at an instant. `identity` makes the record carry the subscriber's identity, read at the instant of allocation, so that no one has to cross-reference two databases under pressure.

| Command | Where the identity comes from |
|---------|-------------------------------|
| `identity file <path>` | a file with one line per subscriber: `<inside-address> <identity>` |
| `identity dhcp-leases <path>` | the DHCP server's leases file (ISC dhcpd format); only active leases count |
| `identity radius secret <secret> [port <n>]` | the RADIUS accounting the BNG already sends; without `port`, 1813 |
| `identity radius disable` | stops listening to accounting |
| `identity none` | no table: every record says `unknown` |

The box is **not** a RADIUS server: it only listens to accounting, checks each packet against the shared secret and discards without reply what does not check out. Without `secret` `commit` refuses the line.

The path is configuration; the file's content is not, and **no screen shows an identity**. `show cgnat identity` says how many the box has and when it read them, and the RADIUS secret comes out as `<set, not shown>` in `show running-config`.

A dynamic pool, or deterministic with `reserve`, on a box that exports nothing gets a warning at `commit check`: without the record, there is no way to say who was using a port.

## How the box writes it

`show running-config`, `show configuration` and the file `save` writes carry the whole block, the way it is typed:

```
! cgnat
nat
 cgnat
  pool residencial
   prefix-inside 100.64.0.0/22
   prefix-outside 203.0.113.0/28
   block-size 512
   policy interleaved
   mode deterministic
   exit
  exit
 exit
interface ens18
 cgnat inside
 exit
interface ens19
 cgnat outside
 exit
```

Inside `nat` / `cgnat` are the pools and then the global settings. The side of each interface appears in its block, along with the address, the description and the MTU: whoever reads a port sees right there that subscribers arrive through it.

## Enabling on an interface

The CGNAT side is a property of the interface and is typed in its block:

```
prisma# [edit] interface ens18
prisma# [edit interface ens18] cgnat inside
prisma# [edit interface ens18] exit
prisma# [edit] interface ens19
prisma# [edit interface ens19] cgnat outside
prisma# [edit interface ens19] exit
```

`inside` is the port subscribers arrive through; `outside`, the port translated traffic goes out through. `no cgnat`, inside the block, takes the interface out of CGNAT.

The old form, `interface ens18 inside` under `nat` / `cgnat`, is still accepted, so that runbooks and scripts already written keep working. The box tells you where the line will appear:

```
prisma# [edit nat / cgnat] interface ens18 inside
  note: kept as 'cgnat inside' in the block of interface ens18
```

A file saved by an earlier version, with the lines inside `cgnat`, is read normally, and the next `save` writes each one into its interface's block.


:::info
**The card that goes to the dataplane is not chosen here.** It is named in `system` > `dataplane` > `interface <card>`, and `show hardware` marks which of them carries the default route — the path through which the box is managed. Naming that card is accepted, and `commit` says whether handing it over drops the session you are in; on a single-card box, the management address goes along to the pair the dataplane maintains. The step by step is in [Getting started](/s/wiki-en/doc/getting-started-WksYlCtcGd).

:::

# See also

* [Pool configuration](/s/wiki-en/doc/pool-configuration-wkJ1DcmG2Y) — the fields inside the pool
* [Configure CGNAT step by step](/s/wiki-en/doc/configure-cgnat-step-by-step-jU9hEJYMch) — from zero to the first translated subscriber
* [commit, rollback and save](/s/wiki-en/doc/commit-rollback-and-save-HVsz5GElE1) — how to apply what was configured here
