# uRPF and spoofed sources

What happens when a packet arrives with a source address the sender does not own, why that is a problem of **evidence** and not only of security, and what uRPF solves — and what it does not.

# Why this matters more here than on an ordinary router

Every other property of this equipment exists to make the log **answerable**. This one exists to keep it from being confidently **wrong**.

A packet that arrives on an inside interface with a spoofed source is translated like any other, gets a port from that address's block and — in deterministic mode — is attributed to that subscriber by arithmetic, for as long as anyone cares to ask.


:::warning
The record is **indistinguishable from a genuine one**, because nothing in the system knows the difference. A court order answered with that record points to a subscriber who did nothing.

:::

# The defense: uRPF

The check is *unicast Reverse Path Forwarding*: the source address must **route back over the path the packet came from**. It is a feature of VPP itself, enabled per interface and **off by default**.

```
configure
 interface enp2s0
  urpf strict
  exit
 commit
```

The check goes on the **inside** interface, the one with `cgnat inside` in its block. `urpf off`, or `no urpf`, turns it off again. The change is applied immediately, without dropping sessions, and goes to the file with the next `save`.

| Mode | Accepts | Where to use |
|------|---------|--------------|
| `strict` | the route to the source points to the interface the packet arrived on | the inside interface of a CGNAT |
| `loose` | the source has a route somewhere | asymmetric topologies, where `strict` would drop traffic that is correct |
| `off`, or absent (default) | anything | where the return path is not known |


:::info
**Off by default on purpose.** Turning on `strict` in a topology with asymmetric return paths drops traffic that was working a moment ago — and does it silently: there is no symptom other than some things stop working. That decision belongs to whoever knows the topology.

:::

The check runs **before** translation, so a refused packet never reaches the CGNAT: no port is taken from the block it was aiming at, and no session exists to be attributed to anyone.

That is why the uRPF drop **does not appear** in `show cgnat drops`: that screen counts only what the CGNAT itself refused, and the packet stopped here never reached it. The two are different things — one is an attack or a misconfiguration; the other, `no pool for source address`, is an address this box simply does not translate. The uRPF drop goes into the interface's own drop counter, in `show interface <name>`.

# Where the protection stops


:::warning
**A subscriber spoofing a neighbor's address is not caught.** In the common topology, every subscriber in a pool is reachable through the same inside interface — so the FIB answers "yes, that way" for the victim's address exactly as it does for the attacker's. `strict` lets it through, and the mapping is attributed to the neighbor.

:::

That is the limit of what a **route** check can see, and it is not a gap that a better uRPF would close. Catching it requires:

* the address bound to the subscriber it was handed to — correlation with RADIUS or DHCP; or
* the access network refusing the spoofing before the packet gets here, which is where it is cheapest to do.

What uRPF **does catch** is everything from outside the subscriber space injected on an inside link, and traffic from one access link claiming addresses that belong to another. On a box with more than one inside interface, that is most of it.

This limit is **verified**, not merely described: the test sends a neighbor's address over the right interface and requires it to be translated — so the boundary would fail as a test if anyone ever claimed otherwise.

# How to check

```
show interface enp2s0
```

The interface screen has a `urpf` line with the mode the **dataplane** is applying — `strict`, `loose` or `off` —, read from the box and not from the file, and the port counters, where the drops are counted.

The boot self-test checks the same thing at every boot: when the saved configuration turns uRPF on for some interface, it says whether the dataplane actually has it on. With everything right, the line is:

```
  [ ok ] uRPF is on in the dataplane, as the saved configuration asks
```

If the configuration asks for it and the dataplane does not have it, the self-test reports it as a failure — a protection that simply is not there has no counter to give it away.


:::success
With `urpf strict` on the inside interface, `show interface` confirming the mode and legitimate traffic passing normally (`show cgnat top` with the usual subscribers): you have closed the door to spoofed sources coming from outside the subscriber space, without dropping anyone who was legitimate.

:::

# See also

* [Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn) — the `interface` block where `urpf` is configured
* [IPFIX logging and audit trail](/s/wiki-en/doc/ipfix-logging-and-audit-trail-mrQPyuvc8g) — the record this protects
* [Operations and troubleshooting](/s/wiki-en/doc/operations-and-troubleshooting-kql3GmIqmX) — where to start when a subscriber disappears
