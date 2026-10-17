# Timeouts and the RFC floors

How long each type of session lives, what floors the RFCs set, and what breaks when you go below them. For anyone whose pool is filling up and who thought about shortening a timeout.

# The temptation, and what it costs

Shortening a timeout is the first thing anyone thinks of when the pool fills up, and it works: ports recycle faster and more subscribers fit on one public address.

What it also does is cut connections that are **idle, not finished** — an SSH session left open over lunch, a database pool, a sensor that talks once an hour. These come back weeks later as *"disconnects on its own, randomly"*, and nobody ties it to a number changed in another month.

# The floors

| Timeout | Floor | Reference | Default | What breaks below it |
|---------|------:|-----------|--------:|----------------------|
| `udp`   | 120 s | RFC 4787 REQ-5 | 300 s   | a mapping closing while the application still believes it exists |
| `icmp`  | 60 s  | RFC 5508 REQ-2 | 60 s    | an echo reply arriving after the mapping closed — it reads as packet loss in every path test |
| `tcp-transitory` | 240 s | RFC 5382 REQ-5 | 240 s   | a half-closed connection discarded before the peer finishes closing it |
| `tcp-established` | 7440 s | RFC 5382 REQ-5 | 7440 s  | an idle but live connection, cut |

The defaults are already at or above the floor: a box nobody tuned raises no warning at all.


:::info
These are **SHOULD**s in the RFCs, and the equipment treats them as such: a value below the floor is **accepted**. You may have a reason, and a box that refuses is less useful than the one you are replacing. What it does not do is accept in silence.

:::

The warning goes to **both places** - to whoever configured it and to the log - because the person who shortens a timeout when the pool fills up and the person who diagnoses the random disconnections six weeks later are not the same person. But the two do not receive the same sentence.

On the VTY, `commit` prints one line per value, with the timeout name, the value now in effect and the RFC:

```
warning:  udp                 119   BELOW the floor RFC 4787 REQ-5 asks for
```

**The floor in seconds and what breaks below it go to the log**, not to the commit screen. If you want all three in the same sentence, the log is what answers - the table above covers the rest.

It fires **once per change** — never per packet, never per session. And a value exactly **at** the floor says nothing: a warning that always fires is a warning the operator learns to skip.

# Which timeout applies to a TCP connection, and when

The four floors above do not all apply at the same time. A TCP session changes timeout according to what passes through it, and that is what decides how long a port stays out of circulation after the connection has ended:

| Connection state | Timeout in effect | Why |
|------------------|-------------------|-----|
| SYN sent, never established | `tcp-syn` (20 s by default) | a host scanning the internet produces one of these per probe; held by the transitory timeout, they would exhaust the subscriber's own block |
| established      | `tcp-established` | the connection is alive, even if idle |
| FIN seen, from either side | `tcp-transitory`  | someone started closing: it leaves the long timeout right away |
| RST seen, from either side | none — the port is released | a reset ended the connection; there is nothing to hold |

The practical reading is this: a connection ended with a **reset** returns the port almost immediately; one ended with a normal close waits for the transitory timeout, not the two hours; and only a connection that **nobody closed** stays on `tcp-established`.


:::info
This matters when a subscriber fills the block without traffic that justifies it. If the ports were held by connections already ended, the number would drop on its own within a few minutes. A block that does not empty is a block full of sessions nobody closed — an application that abandons sockets, or an infected host — and not leftovers of what already finished.

:::

Tracking applies **in both directions**: the reset or the close counts wherever it comes from, because the one who ends a connection may be the subscriber or the other side.

# Configuring

Global, at the `nat` > `cgnat` level — only the four timeouts with a floor:

```
configure
 nat
  cgnat
   timeout udp 300
   timeout icmp 60
   timeout tcp-transitory 240
   timeout tcp-established 7440
   exit
  exit
 commit
```

Per pool — overrides the global; if absent, it inherits. A pool accepts the four above plus four more that exist only there:

```
configure
 nat
  cgnat
   pool residencial
    timeout udp 300
    timeout tcp-syn 60
    timeout gre 3600
    timeout port-holddown 8
    timeout alg-expect 30
    exit
   exit
  exit
 commit
```

| Pool-only timeout | Default | What expires |
|-------------------|--------:|--------------|
| `tcp-syn`         | 20 s    | half-open TCP, before the handshake completes |
| `gre`             | 1800 s  | the GRE mapping of a PPTP call, idle |
| `port-holddown`   | 4 s     | how long a released port waits before being reused |
| `alg-expect`      | 60 s    | how long a media port announced by an ALG waits for the first packet |

`port-holddown` exists so that a delayed segment from the connection that ended does not land in the new connection that took the same port — which the subscriber sees as a reset nobody can reproduce. The port returns to the block of the **same** subscriber, so this does not change who answers for it.

## Block retention, in BPA

In a BPA pool (`mode bpa`), a block that went idle does not return to the pool right away: it stays reserved to the subscriber for `block-holddown` seconds, global, at the `nat` > `cgnat` level. The default is 120 s.

```
configure
 nat
  cgnat
   block-holddown 120
   exit
  exit
 commit
```

It is the main lever on log volume: each release and each new allocation is an IPFIX record, and with a retention that is too short a subscriber who pauses between two packets releases and reallocates the block all the time — two extra records at the collector on every pause. The minimum accepted value is 1 s. In a deterministic pool, it applies only to the `reserve` blocks: the block the formula gives each subscriber is never released.

All of these are settings **applied in place**: they do not drop sessions.

# How to check

```
show this
```

Inside `nat` > `cgnat`, or inside the pool, `show this` shows the timeouts configured at that level. And `commit` warns: **every value below the floor** is flagged at the moment the change is applied, global and per pool with the overrides already resolved, so that the state is visible to whoever did not know they should ask.


:::success
No value flagged below the floor, and `show cgnat top` showing subscribers far from the block limit: the pool is sized without needing to tighten timeouts.

:::

If the pool is filling up, shortening a timeout is the last resort, not the first. Before it: [grow capacity with a second pool](/s/wiki-en/doc/expanding-capacity-without-dropping-subscribers-R1zYvsfLCT), reduce `block-size` in a window, or limit whoever is consuming too much with `max-sessions`.

# See also

* [Expanding capacity without dropping subscribers](/s/wiki-en/doc/expanding-capacity-without-dropping-subscribers-R1zYvsfLCT) — the alternative to tightening timeouts
* [Pool configuration](/s/wiki-en/doc/pool-configuration-wkJ1DcmG2Y) — the per-pool fields
* [NAT behavior and compatibility](/s/wiki-en/doc/nat-behavior-and-compatibility-GcSwkjjgUa) — the other common cause of "disconnects on its own"
