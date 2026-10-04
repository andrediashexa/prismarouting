# NAT64: the CGNAT with an IPv6 inside

| | |
|---|---|
| **Who** | Whoever builds the NAT64 phases of #485; read by whoever reviews them. |
| **What** | What a NAT64 pool is in this product, what exists today, and what each phase adds. |
| **Where** | `src/plugins/bpa/bpa_nat64.h`, `ref/bpa_model.py`, `test/test_bpa_nat64.c`; the plan is on issue #485. |
| **When** | NAT64 ships from 0.150 on; phase 1 is planned for 0.150.0. No NAT64 pool can be configured until phase 2. |
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

## How it is tested

`ref/bpa_model.py` computes the subscriber index and the RFC 6052 embedding
from the specifications, `ref/gen_nat64_vectors.py` writes its answers to
`test/bpa_nat64_vectors.h`, and `test/test_bpa_nat64.c` holds the dataplane's
header to them, RFC 6052's own section 2.4 table included, plus the rules no
vector shows: the u octet, the Well-Known Prefix's refusals and the key's
packing.

## Phases

The plan, phase by phase from 0.150 on, is on issue #485: the arithmetic (this
page); the datapath with the pool's VTY grammar and a lab; dynamic mode and
the log; every per-pool statement supported or refused; the ALGs; PREF64 and
a 464XLAT lab.
