<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/02-horizontal/prisma-horizontal-negative-transparent.png">
    <img alt="Prisma Routing" src="docs/brand/02-horizontal/prisma-horizontal-positive-transparent.png" width="420">
  </picture>
</p>

# Prisma Routing

## TL;DR

Software router with built-in CGNAT by **port block**: deterministic (the block
is a formula, and no log is needed) or **BPA** (*bulk port allocation*, blocks
allocated on demand in steps, and logged). VPP/DPDK dataplane, FRR control plane, IPFIX logging
per RFC 8158, single VTY. Targets 100 Gbps+ and 200k+ subscribers, shipped as
an installable Debian appliance. Called HexaRouting until 0.76.1; the rename
was a clean break, so the paths, the units and the `prismash` command carry
the new name and nothing migrates from the old one
([#202](https://github.com/andrediashexa/hexarouting/issues/202)). Issue links
written before the repository was renamed still say `hexarouting`, and GitHub
redirects them.

Verified on real hardware with real Internet traffic: subscribers translated
to the exact public addresses the reference model predicts, ICMP and HTTPS
flowing, BGP/OSPF/IS-IS routes reaching the VPP FIB.

**Working on this repository?** [CLAUDE.md](CLAUDE.md) is the entry point: the
rules, where each one is written down, the check that refuses a violation, and
the conventions that are written nowhere else. [docs/context.md](docs/context.md)
is what is not obvious from any single file, and [docs/handoff.md](docs/handoff.md)
is where the last session left off.

```sh
make test              # the core suites: no root, no VPP required
sh tools/lab.sh        # list the labs
sh tools/run-all-labs.sh   # the whole dataplane suite, several at a time
sh tools/vty.sh        # open the VTY
make release-gate      # build the installation medium from a tag and boot it
```

## 5W2H

| | |
|---|---|
| **What** | An IPv4 software router whose CGNAT allocates a contiguous **port block** per subscriber instead of logging every flow. |
| **Why** | Brazilian regulation (Marco Civil, Law 12.965/2014) requires one year of connection records to be kept. Per-flow NAT logging produces terabytes of them; a port block reduces this to one record per block under BPA — or to **zero** in deterministic mode, where the proof is the algorithm and the dated configuration instead. The box **exports** the records and keeps none: whoever retains them for the year is the collector. |
| **Who** | ISPs deploying CGNAT. The product is installed per provider, not multi-tenant. |
| **When** | Deployed when public IPv4 runs out and subscribers must share addresses. |
| **Where** | On the provider's border, between the subscriber network (RFC 6598) and the upstream. |
| **How** | VPP plugin (`bpa`) performs translation; FRR handles routing via `linux_cp`; `prismash` provides one CLI for both. |
| **How much** | 200k subscribers in deterministic mode with 512-port blocks require a `/21` of public IPv4. Dynamic mode with oversubscription needs far less — see [docs/logging.md](docs/logging.md). |

## Architecture at a glance

```mermaid
flowchart LR
    SUB["Subscribers<br/>100.64.0.0/10"] -->|LAN| VPP

    subgraph BOX["Prisma Routing appliance"]
        VPP["VPP dataplane<br/>bpa plugin"]
        FRR["FRR<br/>bgpd / ospfd / ospf6d / isisd"]
        VTY["prismash<br/>single VTY"]
        VPP <-->|linux_cp TAP| FRR
        VTY -.->|config| VPP
        VTY -.->|config| FRR
    end

    VPP -->|WAN| NET["Internet"]
    VPP -->|IPFIX RFC 8158| COL["Collector"]
```

## Layout

```
CONTRIBUTING.md        project rules: documentation, issues, branches, releases
CHANGELOG.md           what changed in each released version
VERSION                single source of truth for the version
docs/context.md        start here: the invariants, the map, the reading order
docs/architecture.md   design decisions, sharding, mapping algorithm
docs/routing.md        FRR integration, VTY, punt pitfalls
docs/logging.md        BPA log export over IPFIX (RFC 8158)
docs/appliance.md      boot tuning, systemd, install, ISO
docs/roadmap.md        what a CGNAT still needs, and why each item matters
docs/ha.md             high availability: what is built and measured, and what is still a plan
docs/bng.md            BNG analysis: what VPP gives and what it would cost (NOT implemented)
docs/wiki-audit.md     the brief for auditing the wiki against this repository
PADRAO-DOCUMENTACAO.md how a wiki page is written (not the same as CONTRIBUTING)
wiki/                  mirror of the wiki; tools/wiki-status.sh says what is unpublished
ref/                   executable reference model of BPA, in Python
src/plugins/bpa/       the VPP CGNAT plugin
src/prismash/          the VTY
test/                  core tests: no VPP, no root
lab/                   executable labs, from packet injection to real traffic
appliance/             boot tuning, systemd units, installer, ISO builder
tools/                 build and lab helpers
```

## Documentation

| Where | What it is |
|---|---|
| [docs/context.md](docs/context.md) | **Start here.** The invariants, a map of what question each part answers, and a reading order per task. |
| `docs/`, `README.md`, `TESTING.md` | **Authoritative about the product.** What it does and why. |
| <https://hexarouting.hexanetworks.com.br> | The wiki: how somebody *uses* it. A usage layer, and currently a **draft** — see below. |

The wiki is public and still being written. It is **not** a source: where a page
disagrees with `docs/`, the page is a defect. `wiki/` here is a read-only mirror
so its pages can be read and diffed without leaving the repository;
[docs/wiki-audit.md](docs/wiki-audit.md) is the brief for correcting it.

## Getting started

**Start with [docs/context.md](docs/context.md)** — the eleven facts that make
the rest of the repository legible, a map of what question each part answers,
and a reading order per task. It does not repeat the design; it says which file
is authoritative for each decision.

Then **[TESTING.md](TESTING.md)** for the four testing levels and what class of
defect each one catches.

```sh
sh tools/build.sh image     # build container, VPP pinned to 26.06
sh tools/build.sh plugin    # compile the plugin
make test                   # core tests
sh tools/lab.sh datapath    # packet-level translation
make iso                    # the installation medium
make upgrade-artifact       # what a box already in service takes
```

The ISO is an installation medium, not an appliance: it boots with no
credentials, and `install` in the VTY asks for the disk, hostname, timezone and
root password before it writes anything. See
[docs/appliance.md](docs/appliance.md#installing-to-disk).

## Status

The current version is in [`VERSION`](VERSION) — deliberately not repeated
here, since a number written in two places becomes two numbers. The project
stays on `0.x.x` until it is declared ready for production; see
[CONTRIBUTING.md](CONTRIBUTING.md) section 5 and [CHANGELOG.md](CHANGELOG.md).

| Component | State |
|---|---|
| BPA mapping core | Verified against the Python reference model, every mapping in the pool one by one; `make test` prints the counts |
| Dataplane (translation, handoff, dynamic mode, fragments, ICMP errors, TCP state) | Verified with injected and real traffic |
| Routing (BGP, OSPF, IS-IS) | Verified — routes reach the VPP FIB |
| IPFIX logging (UDP and TCP) | Verified with a decoding collector |
| VTY (`prismash`) | Candidate/`commit`/`rollback`, roles, `show route` against the FIB, `show this`, and completion of live values — interfaces from the dataplane, pools from the candidate. A statement is refused **where it is typed** at every level: this VTY's own grammar for what it owns, and FRR's whole grammar (`vtysh --dryrun`, no daemon needed) for everything it hands to FRR ([#431](https://github.com/andrediashexa/hexarouting/issues/431)) |
| Configuration trail | Attributable and tamper-evident: every entry says **who asked**, and one that cannot be attributed says so rather than leaving the field off; chained by SHA-256. A change typed at the dataplane's own CLI has no session to ask, and is recorded `by unattributed via cli` |
| Appliance (ISO, tuning, systemd) | ISO boots clean, self-test reports ready; **since v0.28.0** every release is built from its tag, booted, installed to a disk and booted again from that disk before publishing, by `tools/release-gate.sh`. The 26 releases before it, v0.2.0 to v0.27.0, were published without that gate |
| Connection records | On their own filesystem, so a full log cannot stop the box working |
| **HA / failover** | **Active/standby, and the acceptance test is green**: a virtual router on the dataplane interface (RFC 5798, VPP's own plugin) carries a service address and its virtual MAC to the peer, so a CPE that never flushed its ARP cache follows. Measured on a pair, in `lab/ha-failover.sh`: a pulled uplink, a stopped dataplane and a killed one are all survived, with every subscriber keeping the same public address and the same port block, and a commit that would map subscribers differently from the peer is refused. **Sessions are not synchronised** - connections drop and re-establish, which is what this design claims. **Not measured here**: whether a real NIC's driver reports a pulled cable, which needs hardware ([#28](https://github.com/andrediashexa/hexarouting/issues/28)). See [docs/ha.md](docs/ha.md) |
| **Throughput** | **Bits per second NOT measured** - that needs a NIC DPDK drives natively; see [#28](https://github.com/andrediashexa/hexarouting/issues/28). What a packet costs the translation nodes in CPU clocks IS measured, by `lab/throughput.sh` against VPP's own counters, and [docs/roadmap.md](docs/roadmap.md) section 3.5 carries the figure |

What is planned, in which release, and why in that order:
[docs/roadmap.md](docs/roadmap.md) section 5.
