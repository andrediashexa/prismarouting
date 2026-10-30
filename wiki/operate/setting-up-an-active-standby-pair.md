# Setting up an active/standby pair

How to have two Prisma Routing boxes hold the same service address, one active and the other standby, and how to prove the switchover works before you need it. For those who already have both boxes installed and forwarding; the reason behind each choice is in [High availability](/s/wiki-en/doc/high-availability-cMUVBBTFgL).


:::info
**The pair preserves the address, not the connection.** There is no session sync: connections in progress drop at the switchover, as with any NAT. On a **deterministic** pool the subscriber comes back on the **same public address and the same port block**, because the block is a formula of the configuration. On a **BPA** pool (dynamic allocation) the box that takes over does not know which blocks the other one had handed out: the subscriber gets **a new block**, allocated and recorded by the box that took over.

:::

In the examples, box A is the one that should hold the service and box B is the standby. `enp1s0` is the inside port (subscribers) and `enp2s0` the outside port (upstream); replace them with your boxes' names. The port names do not need to match on both: the pair is matched by the virtual router number and by the address, never by the interface name.

# 1. The same pools on both boxes

Configure the CGNAT pools **exactly the same** on both boxes: inside and outside prefixes (typed or taken from a `prefix-list`), `block-size`, `ports`, `policy`, `mode`, `reserve` and the VRFs — and, on a NAT64 pool, `subscriber-length` and `nat64-prefix`. These are the fields that decide where each subscriber lands, and two boxes that disagree on them fail **silently** — the switchover puts subscribers into blocks the other never used, and neither of them records anything abnormal.

What does not touch the mapping can differ without problem: timeouts, ALGs, TCP clamp, bypass lists, session and rate limits, port forwards, `block-stride`, utilization alarm.

How to configure each field is in [Pool configuration](/s/wiki-en/doc/pool-configuration-wkJ1DcmG2Y).

# 2. `vrrp` on the inside and outside ports

On each port that neighbors use as a gateway, declare a virtual router (RFC 5798) with a service address that **neither box** has as its own address. Each box keeps its own address on the port; the service address is what moves.

On box A, higher priority, and each port tracking (`track`) the other:

```
configure
 interface enp1s0
  ipv4 address 100.64.3.252/22
  vrrp 1 address 100.64.3.254
  vrrp 1 priority 200
  vrrp 1 track enp2s0 decrement 150
  exit
 interface enp2s0
  ipv4 address 198.51.100.2/29
  vrrp 2 address 198.51.100.1
  vrrp 2 priority 200
  vrrp 2 track enp1s0 decrement 150
  exit
```

On box B, the **same virtual router numbers and the same service addresses**, with each port's own address and a **different** priority — the default, 100, will do:

```
configure
 interface enp1s0
  ipv4 address 100.64.3.253/22
  vrrp 1 address 100.64.3.254
  vrrp 1 track enp2s0 decrement 150
  exit
 interface enp2s0
  ipv4 address 198.51.100.3/29
  vrrp 2 address 198.51.100.1
  vrrp 2 track enp1s0 decrement 150
  exit
```

What each line changes:

| Line | Range | What it does for you |
|------|-------|----------------------|
| `vrrp <id> address <A.B.C.D>` | id from 1 to 255 | the address subscribers (or the upstream) use as gateway; answers ARP with the virtual MAC `00:00:5e:00:01:<id>`, which moves between boxes along with it |
| `vrrp <id> priority <n>` | 1 to 254, default 100 | who holds the service: the highest wins |
| `vrrp <id> track <interface> decrement <n>` | 1 to 254 | if the tracked interface goes down, the box advertises the priority minus `n`. With 200 and `decrement 150`, box A drops to 50 and loses to B's 100 |

**Without** `**track**`**, the pair survives a dead box but not a deaf one.** A box that lost the upstream and is still alive would keep the inside address, receiving the subscribers with nowhere to send them.

**Equal priorities on both boxes work, but the addressing decides**, not you: the RFC breaks the tie by the highest address. Use different priorities.

The advertisement interval is not configurable: it is the RFC default, one second, and it must be the same on both boxes. Takeover takes about three and a half seconds.

**A NAT64 pool in the pair needs the IPv6 gateway too.** An IPv6 address under `vrrp <id> address` builds, beside the IPv4 router, the IPv6 virtual router of the same id, with the same priority, the same `track` and the same `preempt`. The first IPv6 address must be a link-local one (for example `vrrp 1 address fe80::1`, before the global one) — `commit check` refuses an IPv6 router without it —, and that link-local is what the IPv6 subscribers, or the BNG in front of them, point their gateway at. Its virtual MAC is `00:00:5e:00:02:<id>`. `track` is per family: an IPv4 router tracking an interface with no IPv4 address never sees it up, and `commit check` warns, naming the interface.


:::info
**The virtual address does not answer ping.** The box does not turn on *accept* mode, and RFC 5798 says not to answer ICMP in that case. Forwarding **through** it works normally. Your monitoring should ping each box's own address, which is what tells you whether it is alive.

:::

# 3. `commit`, and what `commit check` refuses

Before `commit`, `commit check` checks the candidate. Two refusals are specific to `vrrp`, and both come out already at `check` — not in the middle of applying:

A virtual router with no address:

```
error: interface enp1s0: vrrp 1 has no address
    a virtual router with nothing to stand for would elect a master that
    answers for nothing
```

A `track` pointing to an interface the box does not have:

```
error: interface enp1s0: vrrp 1: no interface enp5s0
    the dataplane does not have the interface this virtual router was told to
    track
```

And `vrrp` on an interface the box does not have is refused with the same words as any other line on a nonexistent interface. Fix it and run `commit`.


:::warning
**Changing a virtual router that already exists costs two elections.** The router is not edited in place: the box deletes it and recreates it. If it holds the service address, the peer takes over during the rebuild and `preempt` hands it back right after. The box warns before applying:

```
  changing vrrp 1 on enp1s0 restarts it: this box stops advertising while it is
  rebuilt, the peer takes the address, and preempt hands it back - two
  elections, a few seconds apart
```

Connections in progress drop at both switchovers. Creating a new router costs nothing; changing any field of an existing one is a maintenance-window change.

:::

# 4. `save` on each box, and the copy to the other

`commit` makes it take effect now; `save` makes it survive the boot. Run `save` on both boxes:

```
save
```

The box answers `configuration saved to /etc/prisma-routing/prisma-routing.conf`.

For one box to check the other, it needs to have the other's configuration. VRRP carries no configuration at all, so it arrives as a file: copy the file that box A's `save` wrote to `/etc/prisma-routing/peer.conf` on box B, and B's to the same path on A. The copy is done with `scp` by an administrator account — `root` or `prisma-admin` go through a real shell; from inside the VTY, `start shell` opens one. On box B, for example:

```
scp prisma-admin@<box-a>:/etc/prisma-routing/prisma-routing.conf /etc/prisma-routing/peer.conf
```


:::warning
**The file carries the password hashes and public keys of the other box's accounts.** Do not leave `peer.conf` world-readable, and do not copy this file outside the two boxes.

:::

**Copy it again whenever you change the other box.** There is no session between the two: `peer.conf` is whatever was copied last, and `show cgnat ha` prints the copy date precisely so that a stale file shows up.

# 5. `show cgnat ha` on both boxes

```
show cgnat ha
```

The screen answers three questions, in this order:

**What this box maps** — the mapping fingerprint and the pools:

```
  this box
    mapping fingerprint  <digest>
    pools                <pools>
```

**Its role** — each virtual router, with the state, the addresses, the virtual MAC and the priority it is **advertising**. On box A, with everything in place, the state is `master, holding the address`; on B, `backup`. If the advertised priority differs from the configured one, the line gives both numbers:

```
      priority           50 (configured 200, and something it tracks is down)
```

That is an unplugged cable, not a VRRP problem.

**The peer** — read from `peer.conf`, with the copy date on the `copied` line. With the virtual routers matched:

```
    every virtual router this box carries has one on the peer with the same id,
    the same address and a different priority
```

and with the pools equal:

```
  the two boxes map every subscriber to the same public address and the same
  port block
```

The warnings that can appear instead:

| Warning | What it means | What to do |
|---------|---------------|------------|
| the same priority on both boxes | who holds the service is decided by the addressing | change the priority of one of them |
| a virtual router only one box has | there is no standby for that address | declare the same id and address on the other |
| a pool that does not map the same way | the switchover changes the address and block of that pool's subscribers | make the pool equal on both boxes and copy `peer.conf` again |
| no `peer.conf`, or unreadable | the box does not claim the two agree | make the copy from step 4 |

`show cgnat ha peer <file>` compares against another file instead of `peer.conf`.

# 6. `show interface` gives the port's role

Looking at a single port, during a failure:

```
show interface enp1s0
```

On a port with a virtual router, the screen shows the role and each service address with the virtual MAC:

```
    vrrp 1      master, holding the address
                100.64.3.254 on 00:00:5e:00:01:01
```

On the standby box the line says `backup`. It is the role read from the dataplane, not the configuration. A port without a virtual router shows no line at all.

# 7. The boot self-test

At every boot, the box compares the `vrrp` lines in the saved file with what the dataplane really has, and says on the console:

```
  [ ok ] every virtual router the saved configuration names is on the
         dataplane: 1 holding an address, 1 standing by
```

If the file declares a router the dataplane did not accept, the line is `[FAIL]` and says which (interface and id): the box is in the pair only on paper, and the other holds the service alone, with no standby. The reason for the refusal is in the `prisma-routing` journal. A box without `vrrp` gets no line at all.

# 8. Pool changes on a pair: `commit diverge`

On a box with `vrrp` and a readable `peer.conf`, `commit` **refuses** a change that would make the two map subscribers differently, and says which pool diverges:

```
error: this change maps subscribers differently from the peer
    this box stands for a service address with another one, so a takeover would
    translate them there
```

The refusal ends with `if that is what you mean, type: commit diverge`. Since the two boxes do not get the `commit` at the same instant, every correct expansion goes through this moment. The procedure:


1. On the first box, make the change and confirm with `commit diverge`.
2. On the second, make the same change. With the old `peer.conf`, it may also refuse; use `commit diverge` again.
3. `save` on both and copy both files again (step 4).
4. `show cgnat ha` on both should once again say they map the same.

`commit check` shows the same refusal, so you can decide before typing `commit`. On a box without `vrrp` none of this is a refusal: a `peer.conf` there only produces a warning.

The refusal does not see the other box change after the copy — it only sees the file. Hence the date in `show cgnat ha`.

# 9. `preempt`: whoever comes back, takes over again

`preempt` is always on. When box A comes back — restarted, with the cable back, with the priority restored —, it **takes back** the service address, and connections in progress drop again at this second switchover. The pair ends up where the configuration says, not where the last failure left it. To take the active box out of service without shutting it down, lower its priority, as described in [Day-to-day recipes](/s/wiki-en/doc/day-to-day-recipes-KCc5cOuBip).

# 10. Testing the switchover

Do this in a window, before you need the pair:


1. On both boxes, `show cgnat ha`: A in `master, holding the address`, B in `backup`, the pair matched and the pools equal.
2. Note the public address and block of a test subscriber on box A:

   ```
   show cgnat subscriber <subscriber ip>
   ```
3. Cause the failure on box A. One at a time, with time to recover between them:
   * **pull the upstream cable** (`enp2s0`): proves `track`. Box A should advertise 50 and lose to B;
   * **lower the priority** through the VTY (`vrrp 1 priority 50` and `vrrp 2 priority 50`, `commit`): proves the election;
   * **shut down box A**: proves the loss of the whole box.
4. On box B, `show cgnat ha` should show `master, holding the address`, and the subscriber should be browsing again within a few seconds. Check `show cgnat subscriber` on B: on a deterministic pool, the same public address and the same block; on a BPA pool, a new block.
5. Undo the failure. With `preempt`, box A takes the service back; check both again.


:::info
Detecting the unplugged cable depends on the card reporting the link loss to the dataplane. Check on your card: with the cable out, `show cgnat ha` on box A should show the advertised priority lower than the configured one.

:::

# How to check

On both boxes:

```
show cgnat ha
```


:::success
One box with `master, holding the address` and the other with `backup` on each virtual router; in the peer section, `every virtual router this box carries has one on the peer with the same id, the same address and a different priority`; at the end, `the two boxes map every subscriber to the same public address and the same port block`; and the date in `copied` later than the other box's last change. On the boot console, the line `[ ok ] every virtual router the saved configuration names is on the dataplane`.

:::
