# Port forwarding

How to open an inbound port for a subscriber — camera, small server, game console — without selling them a public address. And why the box refuses some requests.

A subscriber behind CGNAT is not reachable from outside, and some need to be. The ISP's old answer was "buy a public address", which is a lost sale precisely when the ISP has none — which is why the CGNAT is there.

# How to do it

```
configure
 nat
  cgnat
   pool residencial
    forward tcp 6200 to 100.64.0.5 80
    exit
   exit
  exit
 commit
 exit
save
```

```
show cgnat forward
```

It is a setting **applied in place**: it does not drop anyone's sessions.

It also works on a pool with `filter address-dependent`: the forwarded port stays outside that filtering, because whoever arrives through it was never contacted by the subscriber before — that is the purpose of the forward.

On a NAT64 pool, which lives under `nat` > `nat64`, the forward is the same and names an IPv6 address of the subscriber's prefix (`forward tcp 6200 to 2001:db8:1200:ab00::5 80`): whoever arrives from outside over IPv4 reaches that IPv6 host.

# The public address is not your choice

You give the public **port** and never the **address**: the address is the subscriber's own, the one the mapping already assigns them.

Two reasons, and both are properties the rest of the box already guarantees:

| Reason | What would break if it were different |
|--------|---------------------------------------|
| **Address pairing** (RFC 4787 REQ-2) | a subscriber who receives traffic on one address and sends from another is not paired, and applications that check where the reply came from would see two different peers. It is worth stating how far the box guarantees this: always, in a deterministic pool without `reserve`, where each subscriber has a single block. In a dynamic pool, and in the extra block that comes out of a `reserve`, when the subscriber fills their block and gets another, the box **looks for a free block on their public address** before taking any other - a preference, not a rule, because refusing the extension would cost the customer traffic instead of costing an address. The `RFC 4787 REQ-2 paired:` line of `show cgnat blocks` shows the figure for your own pool. Forwarding is still refused on any address other than the subscriber's first, which is the conservative side of this |
| **The reverse arithmetic** | it is what recovers the subscriber from (public address, port) without a table lookup. A forward on another address would be a mapping the formula does not produce, and every reader of the trail would compute the wrong answer precisely for those ports |

That is why the public port **must fall inside the block** the pool assigns to that subscriber. A forward outside the block is refused, naming the subscriber whose block it would have taken:

```
public port 2048 belongs to 100.64.0.9's block, not to 100.64.0.5's.
100.64.0.5 holds 200.150.0.17:1536-2047
```

Find out the block before choosing the port:

```
show cgnat subscriber 100.64.0.5
```


:::info
In practice the routine is: look up the subscriber's block, choose a port **inside** it, and configure the forward. The public port is not a number you make up — it is a number you take from their block.

:::

## The same port serves one subscriber per public address

The pool has as many public addresses as you gave it, and hands **the same port range to one subscriber on each address**. So `203.0.113.16:1100` and `203.0.113.17:1100` are not the same thing: they are two sockets, on two addresses, that the pool has already handed to two different customers.

That is why port 1100 can be sold once **on each public address** of the pool:

```
203.0.113.16:1100 -> 10.0.0.4:8080
203.0.113.17:1100 -> 10.0.0.5:80
```

On a pool with four public addresses this is the difference between selling that port to one customer and selling it to four. You do not choose which address the forward lands on: it lands on the subscriber's own address, as always.

What is still refused is **the same socket twice**: the same port, on the same public address, pointing to two places. The refusal names who already has it:

```
public port 1600 on 200.150.0.17 is already forwarded to 100.64.0.5:80
```

And also the opposite, two different forwards pointing to the same port of the same subscriber, because a customer who answers on two public ports is no longer paired.

## How to remove it

```
configure
 nat
  cgnat
   pool residencial
    no forward tcp 6200 to 100.64.0.5 80
    exit
   exit
  exit
 commit
 exit
save
```

`no forward tcp 6200` on its own also works, as long as that port is forwarded on **only one** public address. If it is on more than one, the box refuses and names the subscribers involved, instead of removing the first one it finds. Removing the forward of a customer you did not name is the kind of thing you find out about over the phone:

```
error: forward tcp 6200 is configured for more than one subscriber
    on different public addresses: 100.64.0.5, 100.64.0.6
    name the subscriber: no forward tcp 6200 to <private-ip> <private-port>
```

The addresses on the second line are the **subscribers** - the private addresses you would name in the command -, and not the public ones where the forward landed. They are what the third line asks for, so they are what the second shows.

The refusal comes in three lines of its own, within the console width. For a while it came out as a single sentence, wider than the screen: the terminal wrapped it wherever it happened to, and what got lost was precisely the end, which is where the command to type is.

# On a dynamic pool, it is the block that gets pinned

On a dynamic pool the block is handed out on the first flow and returned after the subscriber goes idle. A port pinned inside it would follow the block to the next subscriber: the forward would keep working and would start reaching **another customer** — worse than not working, because nothing looks broken.

That is why what gets pinned is not the port, it is the **block**. Configuring a forward on a dynamic pool pins that subscriber's block, and a pinned block **is neither returned on idleness nor handed to anyone else** while the forward exists. The command is the same as on the other pools — you still give only the public port.


:::info
Pinning is permanent by definition, and that is the cost: that block leaves the pool's rotation. `show cgnat blocks` counts pinned blocks separately from blocks in use, precisely so you know how much of the pool no idleness will return.

:::

## Three refusals you will run into

| Refusal | What to do |
|---------|------------|
| the block is **with another subscriber right now** — the refusal names who | wait for that customer to go idle, or serve this one through another port/pool. Taking the block would end their live sessions |
| the subscriber is **beyond the pool's block count** | a dynamic pool is oversold on purpose; for that subscriber there is no block. Expand the public set or serve them through a pool that reaches them |
| the port is **outside the block** of that subscriber | the same rule as deterministic pools: look up the block before choosing the port |

The refusal for an occupied block already appears at `commit check`, before any change on the box.

## After a restart

The block comes back pinned, and you do not need to do anything for that. Which block is pinned is not state kept separately: it is deduced from the forward itself in the configuration, so the configuration coming back is enough. There is no second place to fall out of date.

## In the log

Pinning a block is recorded as an **allocation** and unpinning it as a **release**, exactly like any other block (RFC 8158). The event belongs to the block, not to the forward: a second forward inside an already pinned block does not generate another record, and the release only goes out when the block's last forward is removed **and** the block actually returns to the pool. If the subscriber still has sessions on it, the block becomes an ordinary dynamic block and goes out later, on idleness, with its release. In dynamic mode the log is the only record of who held a block, and a block held for a year without a record is a mapping nobody can attribute to a subscriber afterwards — which here is a legal obligation, not a whim.

There is no separate record for the "pin": the configuration trail already notes every `forward` added or removed, with the operator who did it, and the pinned block is deduced from it.

A configuration is applied with the pools first and the export afterwards, which is the order of every boot. That is why, when export is turned on, the box re-announces as an allocation every block pinned at that instant — otherwise the freshly restarted box would hold each of them with no record at all.

## Where to see it

```
show cgnat blocks                    how many blocks are pinned
show cgnat subscriber 100.64.0.5     says the block is pinned by a forward
```

# How to check

```
show cgnat forward
show cgnat subscriber 100.64.0.5
```

Then, from outside, reach the subscriber's public address on the configured port.


:::success
The forward listed on the pool, the port inside the subscriber's block, and the connection coming in from outside: done — without spending a public address.

:::

# See also

* [Pool configuration](/s/wiki-en/doc/pool-configuration-wkJ1DcmG2Y) — the `forward` field and the other ones applied in place
* [When a subscriber's block fills up](/s/wiki-en/doc/when-a-subscribers-block-fills-up-NvSSgpWda1) — the block the port has to come from
* [Day-to-day recipes](/s/wiki-en/doc/day-to-day-recipes-KCc5cOuBip) — the short version of this task
