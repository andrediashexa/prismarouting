# NAT64: the CGNAT with an IPv6 inside

| | |
|---|---|
| **Who** | Whoever builds the NAT64 phases of #485; read by whoever reviews them. |
| **What** | What a NAT64 pool is in this product, what exists today, and what each phase adds. |
| **Where** | `src/plugins/bpa/bpa_nat64.h`, `ref/bpa_model.py`, `test/test_bpa_nat64.c`; the plan is on issue #485. |
| **When** | NAT64 ships from 0.150 on. Phases 1 (the arithmetic) and 2 (the deterministic datapath, the grammar, the lab) are on the branch for 0.150.0. |
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

What phase 2 does not do is refused by name at `commit check` - dynamic mode,
reserve, max-blocks, initial-allocation, block-stride, forwards, bypass, VRF,
tcp-mss, an ALG - and refused again by the dataplane at creation and on every
in-place door (settings, bypass, forwards), so no route reaches a NAT64 pool
with one of them set.

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

## How it is tested

`ref/bpa_model.py` computes the subscriber index and the RFC 6052 embedding
from the specifications, `ref/gen_nat64_vectors.py` writes its answers to
`test/bpa_nat64_vectors.h`, and `test/test_bpa_nat64.c` holds the dataplane's
header to them, RFC 6052's own section 2.4 table included, plus the rules no
vector shows: the u octet, the Well-Known Prefix's refusals and the key's
packing. `test/test_bpa_pool.c` holds what the configuration step builds and
refuses; `test/test_prismash_config.py` the VTY's statements and refusals.

`lab/nat64.sh` puts an IPv6-only subscriber in one network namespace and an
IPv4 server in another, builds the pool through the VTY, and checks nineteen
things on both sides of the box, the subscriber's block against
`ref/bpa_model.py` among them; it passes single-threaded and with two
workers, and its off run goes red on all nineteen. `lab/nat64-scale.sh`
measures the memory and the clocks (docs/architecture.md, "Memory").

## Phases

The plan, phase by phase from 0.150 on, is on issue #485: the arithmetic;
the deterministic datapath with the pool's VTY grammar and a lab (both on
this page); dynamic mode and the log; every per-pool statement supported or
refused; the ALGs; PREF64 and a 464XLAT lab.
