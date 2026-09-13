# HexaRouting — High Availability

## TL;DR

**Nothing here is implemented.** This is analysis and a plan.

The relevant finding: in deterministic mode the mapping is a *function of the
configuration*, so two boxes with the same pool produce the same block for the
same subscriber without exchanging a single byte of state. A failover still
drops in-flight connections — as any NAT does — but they re-establish on the
**same public address**. For the subscriber that is the difference between
"I reloaded the page" and "my IP changed and the bank logged me out".

The trap for whoever implements this: in deterministic mode, two boxes with
**different** configuration fail **silently**.

The acceptance test is written and **is red on `master`**: `lab/ha-failover.sh`
stands up two boxes, runs a subscriber's translated flow through a service
address, breaks the active box three different ways, and fails every check that
is the product's. That is the intended result, and the section at the end of
this page says what it measured.

> The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT** and **MAY**
> in this document are to be interpreted as described in
> [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## 5W2H

| | |
|---|---|
| **What** | Failover between two HexaRouting boxes serving the same subscriber base. |
| **Why** | A CGNAT box is a single point of failure for every subscriber behind it. |
| **Who** | The provider's operations team; the subscriber should ideally notice nothing. |
| **When** | On hardware failure, planned maintenance, or software upgrade. |
| **Where** | Between two boxes sharing the same pool configuration and the same upstream. |
| **How** | Proposed: active/standby with VRRP, no session synchronisation. |
| **How much** | Zero state to synchronise for address stability; session synchronisation would cost a control-plane flow comparable to the log itself. |

## What deterministic mode gives for free

In conventional CGNAT the subscriber-to-(public IP, port) mapping is the result
of dynamic allocation: it exists only in the table of the box that created it.
A standby knows nothing, and a failover changes everyone's public address.

```mermaid
flowchart TB
    subgraph D["Deterministic BPA"]
        C1["pool configuration"] --> M1["formula"]
        M1 --> R1["subscriber → block"]
        C2["same configuration<br/>on the standby"] --> M1
    end
    subgraph N["Conventional NAT"]
        A1["dynamic allocation"] --> T1["table on box A only"]
        T1 -.->|"not replicated"| T2["standby knows nothing"]
    end
```

| After failover | Conventional NAT | Deterministic BPA |
|---|---|---|
| Subscriber's public IP | changes | **unchanged** |
| Port block | changes | **unchanged** |
| In-flight connections | dropped | dropped |
| New connections | need fresh allocation | work immediately |
| State to synchronise for the above | the whole table | **none** |

The line that matters: in-flight connections drop in both cases, but under BPA
they re-establish on the same public address.

This is not HA. It is the floor HA starts from, and it is already higher than
usual.

## What is still needed for real failover

Preserving in-flight connections requires synchronising the session table,
because the return path needs the **private port**, and no formula produces
that (see [architecture.md](architecture.md)).

| Design | Cost | What it preserves |
|--------|------|-------------------|
| Active/standby without sync | ~zero | Public address and block; connections drop |
| Session sync over multicast | High: every session create and delete becomes a message | In-flight connections |
| Active/active with subscriber partitioning | Medium | Half the traffic unaffected by one failure |

Session synchronisation is the only one that preserves connections and the most
expensive: on a 200k-subscriber box with hundreds of sessions each, replicating
every create and delete is a control-plane flow comparable to the log itself.

**Dynamic mode adds a requirement**: block allocation would have to be
coordinated, or the two boxes would allocate different blocks to the same
subscriber.

## Proposal for a first version

Active/standby with VRRP on both the public and the internal side, no session
synchronisation, and identical configuration guaranteed by the configuration
trail that already exists.

It is honest about what it delivers: it does not preserve connections, it
preserves addresses. For a provider that already removes the worst symptom of a
CGNAT failover.

To be built:

1. Integration with `keepalived` or FRR's VRRP, coherent with `linux_cp`.
2. Verification that both boxes carry the **same** pool configuration.
3. A `show bpa ha` comparing a configuration hash between peers.
4. A lab test with two instances and a forced failure, confirming the
   subscriber keeps address and block. **Done, and red**: `lab/ha-failover.sh`
   ([#9](https://github.com/andrediashexa/hexarouting/issues/9)), written before
   items 1 to 3 so that they are judged against the requirement rather than the
   requirement being written to fit them.

Item 2 **MUST NOT** be skipped. In deterministic mode, two boxes with different
configuration fail silently: the failover starts mapping subscribers to the
wrong blocks with no visible error, and the logs of both boxes will look
perfectly healthy. Configuration equality is a requirement, not a convenience.

## What the acceptance test measured on `master`

`lab/ha-failover.sh` is the acceptance test for this whole page and it exists
before the implementation, which is what
[roadmap.md](roadmap.md) phase 6 asks for. It is **excluded from
`tools/run-all-labs.sh`**, with the reason recorded in that file's `NOT_LABS`:
it is red because the feature is missing, not because anything is broken.

It arranges two dataplanes on a shared inside LAN and a shared outside LAN,
each with its own addresses, and points a subscriber at a **service address**
that neither box owns. The lab MAY build the topology, start both boxes, and
cause the failure. It **MUST NOT** put the service address on a box, move it,
answer ARP for it, flush the subscriber's ARP cache, or decide which box is
active: that is the product's work, and a lab that does it tests itself.
`lab/lcp.sh` carries the same lesson in its header.

Three findings, each measured against a running pair at 0.67.0.

| What was measured | Result |
|---|---|
| Two boxes, same pool, no state exchanged | Both compute `200.150.16.5 ports 1024-3071` for the same subscriber. The claim at the top of this page holds, on packets. |
| The subscriber's translated round trip through the service address | Never completes. The subscriber's ARP for the gateway stays `INCOMPLETE`, because nothing answers for it. |
| Each of three failures: a pulled uplink, a stopped dataplane, a killed dataplane | The service does not come back within 20 seconds in any of the three. |

Two things the run turned up that whoever implements this has to deal with.

**The VTY has no `vrrp` of its own, so it hands the statement to FRR word for
word.** FRR's VRRP would then run on the `linux_cp` TAP, and the TAP is not
where the packets are: the dataplane interface is. An implementation built that
way moves an address in Linux and leaves the VPP FIB and the BPA feature arcs
on the box that died. This is the incoherence
[#7](https://github.com/andrediashexa/hexarouting/issues/7) names, and the
dataplane already ships the other half of the answer - `vrrp_plugin.so`, RFC
5798, loaded on every box, with no virtual router on it because nothing asks
for one.

**Anything at all under `nat cgnat` is accepted and discarded in silence.**
`nat cgnat ha peer 200.150.0.12 priority 200` passes `commit check`
("configuration check succeeds"), passes `commit` ("commit complete"), and then
appears in no `show running-config` and changes nothing on the box. That is why
the lab asserts nothing about the configuration being *accepted* and everything
about the packet: a check on acceptance would be green on a box that does
nothing. The silent acceptance is a defect of its own, filed separately.
