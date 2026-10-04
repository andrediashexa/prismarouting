# NAT64: the CGNAT with an IPv6 inside

| | |
|---|---|
| **Who** | Whoever builds the NAT64 phases of #485; read by whoever reviews them. |
| **What** | What a NAT64 pool is in this product, what exists today, and what each phase adds. |
| **Where** | `src/plugins/bpa/bpa_nat64.h`, `ref/bpa_model.py`, `test/test_bpa_nat64.c`; the plan is on issue #485. |
| **When** | NAT64 ships from 0.150 on. Phases 1 (the arithmetic), 2 (the deterministic datapath, the grammar, the lab), 3 (dynamic mode and the log) and 4 (every per-pool setting) are on the branch for 0.150.0. |
| **How** | One release per phase; nothing configurable before the datapath that serves it. |

## What it is

Stateful NAT64 (RFC 6146) here is not a second translator beside the CGNAT.
Its outside is the same scarce IPv4 pool, shared by port in blocks, and the
question it must answer is the same one: who held a public address and port
at an instant. So it reuses the session table, the port blocks, the logging
and the audit trail. VPP's own `nat64` plugin is not used: it has its own
tables and per-session logging, and no port blocks.

## The subscriber is a prefix

A NAT64 pool's inside is a set of IPv6 prefixes plus a **subscriber prefix
length**. The subscriber is the delegated prefix, never a /128: a host
address inside a /64 changes with privacy extensions (RFC 8981), so a
deterministic mapping keyed on it cannot exist, while the prefix is stable and
is what RADIUS attributes to a person (Delegated-IPv6-Prefix, RFC 4818).

The prefixes are cut into subscriber prefixes and presented as one dense
index, exactly as the IPv4 set presents addresses: sorted, adjacent ranges
merged, overlaps refused. The capacity rule is the IPv4 one, 2^28 subscribers
per pool, and with IPv6 it is a refusal an operator meets: 2001:db8::/32 in
/56 subscribers is 2^24 and fits; in /64 subscribers it is 2^32 and is refused.

## RFC 6052

One NAT64 prefix per pool, of length 32, 40, 48, 56, 64 or 96, with bits
64-71 (the u octet) zero - in a /96 that octet is inside the prefix itself.
The IPv4 address is embedded from the prefix length on, stepping over the u
octet. Under the Well-Known Prefix 64:ff9b::/96 a non-global IPv4 address
(private, shared, loopback, link local, documentation, benchmarking,
multicast, reserved) is never carried (RFC 6052 section 3.1).

## The inside session key

The host's 128-bit address, the port, the protocol and the FIB are 184 bits,
so a NAT64 session's inside key takes three 64-bit words (a `bihash_24_8`).
The outside key is the IPv4 one, unchanged: a public address belongs to one
pool, NAT44 or NAT64, never both.

## The pool, as an operator writes it

```
nat
 cgnat
  pool v6
   prefix-inside 2001:db8:1200::/40
   subscriber-length 56
   nat64-prefix 64:ff9b:1::/48
   prefix-outside 203.0.113.0/29
   block-size 2048
   exit
```

An IPv6 `prefix-inside` is what makes it a NAT64 pool; an inside is one
family, never both. Without `nat64-prefix` the pool takes `64:ff9b::/96`.
The pool is created by its own dataplane message, `bpa_nat64_pool_add`, and
deleted like any other.

What a NAT64 pool does not take is refused by name at `commit check` - a
bypass, for good, and an ALG, for now - and refused again by the dataplane at
creation and on every in-place door, so no route reaches a NAT64 pool with
one of them set.

## The datapath (phase 2)

`bpa-nat64-in2out` sits on `ip6-unicast` after IPv6 shallow reassembly, on
the inside interfaces, and only while a NAT64 pool exists; with workers,
`bpa-nat64-in2out-handoff` sends each packet to the worker that owns its
subscriber, `bpa_sub_thread()` as for IPv4. The subscriber's prefix gives its
index, the index its block (`bpa_forward()` over the pool's pseudo inside
[0, N)), and a new session takes a port of that block. The reply arrives on
`bpa-out2in` like any other, finds the session by the IPv4 outside key, and
is translated back.

RFC 7915, as built and as `lab/nat64.sh` reads it off the wire:

* TCP and UDP: headers rebuilt, checksums updated incrementally across the
  pseudo-header change. A UDP datagram arriving from IPv4 with checksum 0 is
  given one (4.5): IPv6 has no zero checksum.
* ICMP echo both ways, the identifier being the port; ICMP errors both ways
  with the quoted packet translated too, its addresses and port the
  session's. Packet Too Big's MTU from IPv4 is raised by 20, never below
  1280.
* Fragments: the IPv6 Fragment Header becomes the IPv4 fragment fields and
  back. An IPv4 datagram with DF clear that would be more than 1280 as IPv6 is
  cut to 1280 by `bpa-nat64-frag6` (4.1): the subscriber's path is not known
  to be any larger.
* An unsolicited packet to a NAT64 pool's public port is dropped and counted
  `no session`, with nothing sent back, as on an IPv4 pool; the filtering is
  endpoint-independent unless the pool says otherwise (RFC 6146).

`show cgnat translation <public> <port>` answers with the subscriber's IPv6
prefix, from the formula; `show cgnat subscriber <IPv6 address>` gives its
block; `show cgnat top` names it by its prefix.

## Dynamic mode and the log (phase 3)

`mode dynamic` with `max-blocks`, `initial-allocation` and `block-stride`, as
on an IPv4 pool: bpa-in2out's allocator, run on the subscriber's index - the
pool's pseudo inside address IS the index, so the allocator, the hold-down,
the slices per worker and the lending between them are the IPv4 ones,
unchanged. A subscriber that finds no free block records RFC 8158 natEvent
12 once per interval however many packets are refused (#549).

Every record names the subscriber by its delegated prefix
(docs/logging.md, "Record format"): IPFIX's sourceIPv6Address and
sourceIPv6PrefixLength in a template of their own, syslog's
`subscriber="2001:db8:1200:ab00::/56"`. The prefix is copied into the queued
event at the instant of the event, as the identity is, from the pool's IPv6
set - and on a NAT64 pool the identity is looked up by that prefix, never by
the index, which as an IPv4 address would name an unrelated customer.
RADIUS's Delegated-IPv6-Prefix names a NAT64 subscriber (docs/logging.md,
"Who held the address").

The log container carries NAT64 records like any other: the shared ring's
event grew from 52 to 68 bytes for the prefix (segment version 4), and the
container sends the NAT64 template and records itself.

Releases come from the hold-down's reap, from a pool's drain and from
`bpa stop-drain`, through the one walk every IPv4 release takes. `clear
cgnat subscriber` takes an IPv6 address, and clears by pool and index: it
matched the private address alone, across pools, and a NAT64 subscriber's
index read as an IPv4 address is somebody's.

`show cgnat translation` answers a dynamic NAT64 port with the subscriber's
prefix, now; for a past instant the log is the answer, as for IPv4. For a
deterministic NAT64 pool the formula answers any instant, and the
configuration trail (`show cgnat history`) records the pool's IPv6 inside,
`subscriber-length` and `nat64-prefix`, which is what lets it be recomputed.

## Every other per-pool setting (phase 4)

| Setting | On a NAT64 pool |
|---|---|
| `reserve` | A deterministic pool's extension, out of the reserve, through the same allocator; logged by prefix |
| `forward <proto> <port> to <IPv6 host> <port>` | Pins a public port of the subscriber's own block to one host of its prefix. Its own dataplane message, `bpa_nat64_forward_add_del` |
| `tcp-mss` | Clamped both ways, on the original header. A configured value is used as written; `auto` is 20 less than IPv4's, the IPv6 header's extra |
| `vrf`, `outside-vrf` | The subscribers' IPv6 table, and the IPv4 table translated traffic leaves by. Two tenants' NAT64 pools may share an inside prefix and a NAT64 prefix in different VRFs |
| `max-sessions`, `max-session-rate`, `utilisation-alarm` | As on an IPv4 pool: they act in the generic session code |
| `bypass` | **Refused for good**: an IPv6 packet to the NAT64 prefix reaches IPv4 only translated, so there is no untranslated path for a bypass to name |
| `alg` | Refused until the ALG phase |

A forward names a HOST, one /128 of the subscriber's delegated prefix; the
subscriber is the prefix's index, so the validation, the pin and the reply's
agreement check are the IPv4 code. The screens, the refusals and the
configuration trail name the host. One forward per subscriber and private
port: a second host of the same prefix on the same private port is refused as
a clash, never translated to the first.

**Routing.** A NAT64 pool owes the routing two prefixes: its outside, like
any pool, and its NAT64 prefix on the IPv6 side - subscribers reach IPv4 by
sending to it. `commit check` warns when BGP is configured and nothing
announces the NAT64 prefix in the IPv6 family, with the two lines that do:
`ipv6 route <prefix> blackhole` and `network <prefix>`. The blackhole
catches nothing a pool takes - the translation runs on `ip6-unicast` before
the lookup, which `lab/nat64.sh` measures. The subscribers' own inside
prefix needs a route back, as IPv4 subscribers behind a BNG do.

**High availability.** The pair's mapping comparison covers a NAT64 pool:
`subscriber-length` and `nat64-prefix` are mapping fields, so `show cgnat ha`
and the commit refusal on a diverging pair see them. The failover does not
carry the IPv6 side: the pair moves by VRRP and this box takes IPv4 virtual
routers only, so an IPv6 subscriber's gateway is no virtual address that
follows the master. `commit check` warns when a NAT64 pool is on a box in a
VRRP pair; it does not refuse, because a pair whose IPv6 side follows the
master by routing is a correct design this box cannot see. No lab measures a
NAT64 failover.

## How it is tested

`ref/bpa_model.py` computes the subscriber index and the RFC 6052 embedding
from the specifications, `ref/gen_nat64_vectors.py` writes its answers to
`test/bpa_nat64_vectors.h`, and `test/test_bpa_nat64.c` holds the dataplane's
header to them, RFC 6052's own section 2.4 table included, plus the rules no
vector shows: the u octet, the Well-Known Prefix's refusals and the key's
packing. `test/test_bpa_pool.c` holds what the configuration step builds and
refuses; `test/test_prismash_config.py` the VTY's statements and refusals.
The log's NAT64 shapes are held byte by byte by `test/test_bpa_log_tcp.c`,
`test/test_bpa_log_syslog.c` and `test/test_prisma_logd.c`, the identity
table's delegations by `test/test_bpa_identity.c`, the RADIUS attribute by
`test/test_bpa_radius.c`, the monitor's decoding by `test/test_logmon.py`.

`lab/nat64.sh` puts an IPv6-only subscriber in one network namespace and an
IPv4 server in another, builds the pool through the VTY, and checks 46
things on both sides of the box: the deterministic datapath, the
subscriber's block against `ref/bpa_model.py`, the dynamic pool's log read
off IPFIX and syslog collectors - once with VPP carrying it and once with the
log container - and then each per-pool setting: the reserve, the MSS clamp,
the session cap, the alarm, a second tenant in a VRF, the blackholed NAT64
prefix and a forward reached from IPv4. It passes single-threaded and with
two workers, and its off run goes red on all 46. `lab/nat64-scale.sh`
measures the memory and the clocks (docs/architecture.md, "Memory").

## Phases

The plan, phase by phase from 0.150 on, is on issue #485: the arithmetic;
the deterministic datapath with the pool's VTY grammar and a lab; dynamic
mode and the log; every per-pool statement supported or refused (all four on
this page); the ALGs; PREF64 and a 464XLAT lab.
