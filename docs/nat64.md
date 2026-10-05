# NAT64: the CGNAT with an IPv6 inside

| | |
|---|---|
| **Who** | Whoever builds the NAT64 phases of #485; read by whoever reviews them. |
| **What** | What a NAT64 pool is in this product, what exists today, and what each phase adds. |
| **Where** | `src/plugins/bpa/bpa_nat64.h`, `ref/bpa_model.py`, `test/test_bpa_nat64.c`; FTP64 in `src/plugins/bpa/bpa_ftp.c` and `test/test_bpa_ftp64.c`; PREF64 in `src/prismash/config.py` (`with_pref64`, `take_pref64`) and `lab/pref64.sh`; the plan is on issue #485. |
| **When** | NAT64 ships from 0.150 on. Phases 1 (the arithmetic), 2 (the deterministic datapath, the grammar, the lab), 3 (dynamic mode and the log) and 4 (every per-pool setting) shipped in 0.150.0; phase 5 (FTP64, RFC 6384) in 0.150.1; phase 6 (PREF64 in the advertisements, a 464XLAT lab) is on `cgnat/nat64-pref64`. |
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
 nat64
  pool v6
   prefix-inside 2001:db8:1200::/40
   subscriber-length 56
   nat64-prefix 64:ff9b:1::/48
   prefix-outside 203.0.113.0/29
   block-size 2048
   exit
```

**Under `nat` > `nat64`, since #572** - the maintainer's decision of
2026-10-05, which took NAT64 out of `cgnat`. The form 0.150.0-0.150.11 wrote,
a NAT64 pool inside `cgnat`, is refused at `commit check` and when a saved
file is loaded, naming the new one; nothing moves it automatically. `nat64`
holds pools and nothing else: the interfaces, the logging, the timeouts and
the memory are under `cgnat`, shared by both kinds of pool. A pool name is
one pool across both levels, because the dataplane keeps one pool table keyed
by name (and the log, the trail and `clear` name a pool by it). The model
keeps one `pools` dict; a Pool carries the level it is written under, and
what the dataplane answers is placed by its inside.

The views of NAT64 are `show nat64 pool` (the dataplane's own blocks, the
ones with an `inside6` row), `show nat64 subscriber <IPv6 address>`, `show
nat64 translation <public> <port>` and `clear nat64 subscriber <IPv6
address>`. Each refuses what is not NAT64 and names the `show cgnat` or
`clear cgnat` spelling that answers it. `show cgnat` is unchanged and still
the whole dataplane: NAT44 and NAT64 share the session table, the blocks and
the public space, and `show cgnat top`, `blocks`, `forward`, `alg`, `drops`
and `memory` describe both. Through the API a NAT64 pool is a
`Nat64PoolConfig`, area `nat64` (docs/api.md).

An IPv6 `prefix-inside` is what makes it a NAT64 pool; an inside is one
family, never both. Without `nat64-prefix` the pool takes `64:ff9b::/96`.
The pool is created by its own dataplane message, `bpa_nat64_pool_add`, and
deleted like any other.

What a NAT64 pool does not take is refused by name at `commit check` - a
bypass, and every ALG but FTP, for good - and refused again by the dataplane
at creation and on every in-place door, so no route reaches a NAT64 pool
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

## Dynamic mode and the log (phase 3)

`mode bpa` with `max-blocks`, `initial-allocation` and `block-stride`, as
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
| `alg` | `alg ftp on` is FTP64 (phase 5, below), off unless asked; every other ALG is refused for good |

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

## FTP64 (phase 5)

An IPv6-only host's FTP client speaks EPSV and EPRT (RFC 2428) - an IPv6
client has nothing else to say - and many IPv4 servers answer only PASV and
PORT. The control connection then works and every transfer hangs. RFC 6384
is the ALG that bridges the two, and `alg ftp on` on a NAT64 pool is it:

| The client sends | The server is sent | The client gets back |
|---|---|---|
| `EPSV`, `EPSV 2` | `PASV` | the server's `227 (h,h,h,h,p,p)` as `229 (\|\|\|p\|)`, its address ignored (section 6 SHOULD) |
| `EPSV ALL` | `NOOP` | `504`, written over the NOOP's `200` (sections 6 and 12) |
| `EPSV <other protocol>` | `NOOP` | `522` |
| `EPRT \|2\|<its own address>\|p\|` | `PORT` with the public address and a port of the subscriber's block, mapped to the host and `p` (section 7.2) | the server's answer |
| `EPRT` naming another address, or IPv4 | the same, unchanged (7.2 SHOULD NOT translate) | the server's answer |
| `ALGS STATUS64` / `ENABLE64` / `DISABLE64` | `NOOP` | `216 EPSVEPRT`, or `216 NONE` once disabled (section 11) |
| `AUTH`, accepted with 2xx or 3xx | `AUTH` | the server's answer; nothing is translated after it (section 5) |

**It is off unless asked**, unlike the IPv4 ALGs: RFC 6384 section 4 has an
FTP64 ALG deployed only where it is needed, because with it a conforming
server's EPSV is turned into PASV for nothing. A NAT64 pool is created with
every ALG off, and `alg ftp on` reaches it through `bpa_pool_settings_set`.

**How it is built.** The client's command is rewritten on the packet
`bpa-nat64-in2out` has just translated to IPv4, the server's reply in
`bpa-out2in` on the IPv4 packet before it becomes IPv6 - both with the IPv4
ALGs' splice (`bpa_alg_splice`), so the length change is recorded in the
sender's own numbering and `bpa_tcp_seq_adjust` renumbers both directions of
the rest of the connection. The translation does not touch a TCP sequence
number, so the two compose. A command the box does not pass on goes to the
server as `NOOP`, and the server's `200` to it is rewritten into the answer
the client is owed (section 12's second way): the box never builds a packet
of its own. What the next reply must become is one byte on the control
session (`bpa_session_t.ftp64`). The EPRT's mapping is the IPv4 ALG's
expectation keyed on the IPv6 host: a port of the subscriber's own block, on
the half-open timer until the server connects. Every segment of a control
connection the ALG has lengthened has URG cleared (section 5's MUST: keep
the urgent pointer's meaning or clear it).

**What it does not do**, each allowed by the RFC: section 8's default port
20 (a MAY); a `NOOP` answered with something other than `200` reaches the
client as it is rather than tearing the connection down (a SHOULD); and
Telnet option negotiation is passed through rather than refused, which
section 5 says MUST be refused - no FTP client was found negotiating Telnet
options, and refusing one means building packets. Only the first line of a
segment is translated, the IPv4 ALG's rule: a later line is where a file
name lives. A command split across segments, or a malformed one, is left
alone and counted (`FTP64 commands left untouched`). It acts on the client's
control connection only; a subscriber's own IPv6 FTP server reached from
IPv4 through a forward is not what RFC 6384 covers. A retransmitted reply
that was rewritten is renumbered but not rewritten again, the exposure the
IPv4 ALGs already state for one message.

**The other ALGs are refused for good**, by name, at `commit check` and in
the dataplane: RTSP and SIP carry addresses in SDP, and no RFC translates
them between IPv6 and IPv4 - RFC 6157 has SIP cross the families with ICE and
relays, not with a translator rewriting the body; PPTP's data is GRE and
ESP is ESP, neither of which stateful NAT64 (RFC 6146) carries - IPsec
crosses NAT64 as NAT-T over UDP 4500 with no ALG at all.

## PREF64 and 464XLAT (phase 6)

An IPv6-only subscriber still has IPv4-only software: a game, a VPN client,
an application that opens an IPv4 socket to a literal address. 464XLAT
(RFC 6877) gives it IPv4 through a CLAT on the subscriber's side - a
stateless translator (RFC 7915) that turns the application's IPv4 into IPv6
addressed into the NAT64 prefix, which this box's NAT64 pool then translates
back. The CLAT has to know that prefix, and on a box with several pools, or
a pool on anything but the well-known prefix, it cannot guess it.

**The box tells it, in its router advertisements** - the PREF64 option of
RFC 8781, typed under the subscriber-facing interface:

```
interface ens19
 no ipv6 nd suppress-ra
 ipv6 nd nat64 64:ff9b:1::/48
 exit
```

`ipv6 nd nat64` with no prefix is the well-known `64:ff9b::/96`; the
statement may repeat, one prefix each. `commit check` refuses it on an
interface that does not advertise (there is no advertisement to carry it)
and refuses a prefix no NAT64 pool on the box translates - a CLAT that learns
it would send its IPv4 to a prefix the box drops. RFC 8781's prefix lengths
are RFC 6052's, and the scaled lifetime is left to FRR, which writes three
times the advertising interval as RFC 8781 4.1 asks.

**FRR sends that advertisement, not VPP.** VPP 26.06's router advertisement
has no PREF64 option (VPP's ip6_ra.c, in vnet's ip6-nd, writes the source address,
the MTU and the prefixes); FRR 10.7.1's zebra has `ipv6 nd nat64`, and its
advertisement leaves the box through the interface's linux_cp pair - measured
by `lab/pref64.sh`. So on an interface with `ipv6 nd nat64` the box writes
`no ipv6 nd suppress-ra` and the prefixes into FRR, under the pair's Linux
name, and keeps VPP's own advertisement suppressed there: the subscriber
hears one router, with the option in every advertisement. Without
`ipv6 nd nat64` the interface goes back to VPP's advertisement, as before.
These FRR lines are the box's: `show running-config` prints them as the
interface's statements and never in the routing section, and they are taken
back out of what FRR reads back (`config.take_pref64`).

The API carries them as `InterfaceConfig.pref64`, a list beside
`router_advertisements`.

**What the box does not own: DNS64 and RFC 7050.** A CLAT may also learn the
prefix by resolving `ipv4only.arpa` through a DNS64 resolver. This box is
not the subscriber's resolver, so it cannot answer that query; the resolver
the operator runs must synthesize with the same prefix the box advertises,
or the two methods disagree and RFC 7050's answer is the one some CLATs
prefer. The NAT64 pool's prefix is what to configure there.

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

FTP64's decisions - every command and reply above, RFC 6384's own examples
byte for byte, and the malformed inputs - are held by `test/test_bpa_ftp64.c`,
the refusals by `test/test_bpa_pool.c` and `test/test_prismash_config.py`.
`lab/ftp64.sh` runs Python's ftplib over IPv6 against an IPv4 server that
answers PASV and PORT and refuses EPSV, EPRT and ALGS, and fetches a file
passive and active through the box, reading what the server was sent off its
own log.

PREF64's statements - the value, the file, what FRR is handed and what is
taken back out of its read-back in the order FRR 10.7.1 writes it, the
delta between VPP's advertisement and FRR's, and the commit check - are held
by `test/test_prismash_config.py`; the prompt by
`test/test_prismash_complete.py`, the typed API by `test/test_typedcfg.py`.
`lab/pref64.sh` is the 464XLAT: tayga as the CLAT in the subscriber's
namespace, started with the prefix read off the advertisements the
subscriber hears (option 38, after a router solicitation), and an AF_INET
application pinging and fetching from an IPv4 server through it and the
NAT64 pool. The prefix is `2001:db8:64::/48`, so a CLAT falling back to the
well-known one cannot pass. It also checks that every advertisement carries
the option, that a prefix no pool translates is refused, and that VPP
advertises again once the statement is removed; its off run goes red on 8.

## Phases

The plan, phase by phase from 0.150 on, is on issue #485: the arithmetic;
the deterministic datapath with the pool's VTY grammar and a lab; dynamic
mode and the log; every per-pool statement supported or refused (all four on
this page); the ALGs - FTP64, the one there is (above); PREF64 and a
464XLAT lab (above).
