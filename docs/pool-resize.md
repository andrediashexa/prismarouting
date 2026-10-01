# Prisma Routing - Changing a pool that is carrying traffic

## TL;DR

In deterministic mode a subscriber's public address and port block are a
**function of the pool definition**. Change the definition and the function
changes: adding one public prefix moves every subscriber at that instant and
ends every session on the pool.

Nothing is corrupted and no past answer becomes unresolvable — the
configuration trail records which definition was in force — but every customer
on that pool is disconnected at once, and the box reports nothing unusual,
because nothing unusual happened.

`commit` refuses such a change until it is asked for by name:

```
error: this change re-maps subscribers that already have mappings
    1024 of 1024 subscribers change public address or port block; 61 hold live
    state right now
    100.64.0.0 moves from 200.150.0.16:1024-1535 to 45.170.16.0:1024-1535
    100.64.0.1 moves from 200.150.0.17:1024-1535 to 45.170.16.1:1024-1535
    every session on this pool ends when it is applied. Past answers stay
    resolvable - the configuration trail records which definition was in force
    - but the subscribers do not.
    if that is what you mean, type: commit remap
```

Two examples and not one: that is how many the dataplane carries back
(`ARRAY_LEN (example)` in `bpa_cli.c`).

> The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT** and **MAY**
> in this document are to be interpreted as described in
> [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## 5W2H

| | |
|---|---|
| **What** | How to add or remove public capacity on a deterministic pool without discovering the consequence from a customer. |
| **Why** | It is the sharpest edge in the design, and until now it was undocumented and unwarned. |
| **Who** | Whoever runs out of public addresses, which is everyone eventually. |
| **Where** | This page; the check is in `commit`, the arithmetic in the dataplane. |
| **When** | Before the change, not after. |
| **How** | Measure it with `commit check`, then either accept the outage or use the migration below, which has none. |
| **How much** | The migration costs one extra pool and a period where both are configured. |

## What actually changes

The mapping is `subscriber index -> (public address, port block)`, computed
from the pool's address sets, block size, port range and policy. Anything that
changes the **divisor** changes the answer for nearly everybody.

| Change | What moves |
|---|---|
| add or remove a public prefix | the divisor changes: **every** subscriber |
| change `block-size` | blocks per address change: almost every subscriber — the ones whose block still starts at the same port stay |
| change `ports` | same as block size |
| change `policy` | interleaved and blocked split the index differently: **every** subscriber |
| change `mode` | deterministic and dynamic are not the same mapping at all — and this is the one mapping change `commit` does **not** offer to refuse: `bpa_pool_remap_count()` is only reached when the definition on the box and the candidate are both deterministic, so a mode switch commits without the warning above |
| add or remove an **inside** prefix | changes the subscriber index of every address after it |
| change `reserve` | the blocks the formula may use change. Under `interleaved`, **nothing moves**: the divisor is the public address count, so every subscriber the smaller pool still holds keeps its block, and only the ones above the new capacity — `outside.count × reserve` of them — lose theirs. Under `blocked` the divisor **is** the mapped block count, so it re-divides and moves nearly everybody. Both are capacity changes and both need `commit remap` |
| change `block-stride` | **nothing**: it decides where a subscriber's *next* block goes and `bpa_forward()` never reads it, so no existing subscriber moves - measured across every pool and stride in the model's vectors rather than assumed |
| change `vrf` or `outside-vrf` | **no subscriber moves** and every session still ends: the arithmetic is untouched, so `commit` never asks for `commit remap`, but the pool is rebuilt because these are outside `Pool.lines(in_place=True)` |
| everything else — timeouts, ALGs, `max-blocks`, the MSS clamp, the bounds, forwards, bypass lists, the filter mode | **nothing**: these are in-place settings ([#69](https://github.com/andrediashexa/hexarouting/issues/69)) |

## What does not change

* **Past answers stay resolvable.** `bpa_audit.c` records the complete
  definition, dated in UTC, on every change. A request about last month is
  answered with last month's definition, which is why the trail exists.
* **Nothing is corrupted.** Sessions end; they do not become wrong.
* **The log stays complete.** In dynamic mode the allocation and release events
  are unaffected; in deterministic mode there are none to affect.

## Measure it first

`commit check` reports how many subscribers a proposed definition moves, and
where two of them go. It is not an estimate: the dataplane holds both
definitions and runs the same arithmetic it would run afterwards.

```
nat
 cgnat
  pool residential
   prefix-outside 45.170.16.0/26
   exit
  exit
 exit
commit check
```

An operator told *"this changes the mapping"* hears the box being cautious. An
operator told *"1024 of 1024 subscribers move, and 100.64.0.5 goes from
200.150.0.17:1536-2047 to 200.150.0.33:2048-2559"* knows what they are about to
do. That is the difference this page exists for.

## The procedure that costs an outage

Accept it when the pool is small, the window is agreed, or the alternative
below cannot be arranged.

1. `commit check` and read the count. **Record it in the change ticket** — it
   is the number of customers who will call.
2. Choose a window. Every session on the pool ends: not gracefully, not after a
   timeout, but at the instant of the commit.
3. `commit remap`.
4. Confirm on packets, not on configuration: `show cgnat subscriber <address>`
   for a subscriber you know, and check it holds the block the new definition
   predicts.

The subscribers reconnect on their own — a CPE re-opens its sessions within
seconds — but anything long-lived (a VPN, an SSH session, a download) is gone.

## The procedure that does not

**Add capacity as a second pool rather than growing the first.**

A pool is selected by inside prefix, so a new pool serving a *different* slice
of the subscriber space has its own mapping and does not disturb the existing
one:

```
nat
 cgnat
  pool residential      ← untouched, keeps its mapping
   prefix-inside 100.64.0.0/22
   prefix-outside 200.150.0.16/28
   exit
  pool residential-2    ← the new capacity
   prefix-inside 100.64.4.0/22
   prefix-outside 45.170.16.0/26
   exit
  exit
 exit
```

* Existing subscribers **MUST NOT** be moved between the pools while they are
  in service: moving one is the same re-mapping, one customer at a time.
* New subscribers are addressed into the new pool's inside range as they are
  provisioned. The two pools coexist indefinitely; there is no requirement to
  merge them, and merging them later is exactly the outage this avoids.
* Each pool has its own audit record, so an answer about a subscriber names the
  pool that served it.

The cost is that the subscriber address space has to be sliced in advance, and
that the operator carries two pools instead of one. That is the price of not
disconnecting everybody, and it is usually cheap.

**Multi-tenant boxes have a third option**: bind pools to VRFs
([#79](https://github.com/andrediashexa/hexarouting/issues/79)) so one inside
range can be served by two pools without overlap. That solves tenancy, not
capacity — two pools in the same VRF still may not claim the same range.

## What is deliberately not offered

**A graceful migration.** There is no mode where old sessions keep the old
mapping while new ones take the new: that would mean two mappings for one
subscriber, and the reverse arithmetic — the property the deterministic mode
exists for — could not tell them apart. A box that answered a court order with
*"it depends which of two definitions was in force for that particular
session"* would have given up the thing it was built to provide.

**Automatic capacity growth.** For the same reason: a mapping that changes
without an operator saying so is a mapping nobody can reconstruct.

## Related

* [architecture.md](architecture.md#the-mapping) — the arithmetic itself.
* [logging.md](logging.md#configuration-trail) — why the trail makes past
  answers resolvable across a change like this.
* [#39](https://github.com/andrediashexa/hexarouting/issues/39) — a reserve of
  blocks in deterministic mode, which changes capacity and therefore carries
  exactly the hazard this page describes.
