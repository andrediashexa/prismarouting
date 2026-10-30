# Sizing and requirements

How much machine and how many public addresses your case requires. Read this before buying hardware or settling on the IPv4 block.

# Machine requirements

| Resource | Minimum | Why |
|----------|---------|-----|
| RAM      | **6 GB** (8 GB recommended) | with less, the box boots and does not forward: measured with 4 GB, the dataplane ends up with no card at all |
| CPU      | **SSE4.2** | the VPP binary assumes SSE4.2 and uses AVX when present |
| Network cards | **1**   | one is enough, and the box hands that same card to the dataplane, taking the management address with it; two is the shape of a rack appliance |
| Disk     | **8 GB root, plus the log volume** | the installer never leaves the root with less than 8 GB; the log volume is 30 GB by default and can be smaller (section *Log volume*) |


:::warning
**A CPU without SSE4.2 is not a slow problem, it is a silent problem.** VPP dies with `SIGILL` within milliseconds and, with `Restart=always`, the symptom becomes a restart loop that says nothing about the cause. That is why the boot self-test checks the instruction set **before** looking at VPP.

:::

Measured during validation: with 2 GB the ISO reports `Out of memory: Killed process (vpp)` with VPP holding only **19 MB resident** — it was not the glutton, it was the chosen victim. Installed on disk the consumption drops, because there is no RAM overlay, but the floor for a box that forwards is 6 GB, for the reason in the table above.


:::warning
**Below 6 GB the box does not forward.** On a 4 GB VM it boots in the conservative profile, without hugepages, and the dataplane gets no card at all.

:::

Measured on a VM with 8 cores, after the reboot that applies the tuning:

| RAM | What the box does |
|-----|-------------------|
| 4 GB | boots in the conservative profile, without hugepages: the dataplane gets no card and **forwards nothing** |
| 6 GB | forwards, with a 1 GB heap: about 2.7 million sessions fit |
| 8 GB | forwards, with a 2 GB heap |

Up to version 0.128 a 6 GB VM did not bring VPP up; since 0.129 it does.

# How many network cards

**One card is enough, and that is how most people know this product:** a virtual machine with one virtual card. The dataplane takes the port and the management address goes with it, to the interface the dataplane keeps paired with it on the Linux side. The box stops being reached *beside* the dataplane and starts being reached *through* it, with the same MAC, so nothing on the other end of the cable needs to relearn.

**Two cards is the shape of a rack appliance**, and the difference is not performance: it is where management lives. The box claims every card except the one answered as the management network during installation, and never the last one. With two and a management answer, management sits **beside** the dataplane: restarting the dataplane does not drop your session. With only one, management goes **through** the dataplane, and restarting it — or restarting the box — drops the session along with it; the box becomes reachable again when the dataplane comes up, without anyone typing anything on the console.

| Cards | What the box does | What you pay |
|-------|-------------------|--------------|
| 1     | hands the card to the dataplane and takes the management address to the pair | restarting the dataplane drops the management session until it comes back |
| 2 or more, with management answered during installation | keeps all except the management one | nothing      |
| 2 or more, with management `none` | keeps all of them; the management address, if any, goes to the pair of the card that carried it | the same as the single-card box |

## Card added with the box running

A card put into the machine while the box is running — on a virtual machine, a card added by the hypervisor — forwards **right away**, without restarting anything, if the boot rule would claim it. It comes in with the name Linux gave it, is configured in the VTY like any other, and `show interface` warns that it is in a provisional mode. `system dataplane restart`, in the window you choose, moves that card to the definitive mode: it costs every subscriber's sessions and a few seconds without forwarding, and the screen says so before acting.

## The card must be a PCI device

The dataplane takes the port by its PCI address. An interface that does **not** have a PCI address cannot be handed over: `lo`, a bridge, a bond and a veth do not have one. In that case the box writes `no-pci` in the dataplane file, boots, routes, responds - and forwards nothing. The boot self-test says so instead of letting you find out from the stalled counter.

## The box reads the card before writing its configuration

The boot tuning asks **the card** how many queues it has and what its maximum MTU is, instead of deducing from the processor. Before, it asked for one queue per worker, and on a virtual card, which has **one** queue, that made the device be refused: the box came up owning a card it could not start. The number of queues is corrected from the card.

The MTU is also read and **corrected**. Since 0.152.1 every interface without `mtu` starts at **1500**, the box's default — before it started at VPP's own 9000, and with that `tcp-mss auto` gave 8960 and lowered nothing on a 1500 path. A card that does not accept even the default refuses the device: the port goes into error and forwards nothing. So, when some claimed card has a maximum below 1500, the box makes interfaces start at the **smallest maximum** among the cards it claims. The value applies to all of them: on a box with a 1400 card next to a 9000 one, the 9000 one also starts at 1400, and `mtu <n>` in the interface block raises that port afterwards.

The boot report says what it found, one line per card. On an eight-core machine with a virtual card `enp1s0` with a maximum of 1500:

```
# NIC queues, enp1s0: 1, and this box has 7 worker(s). Capped at 1: only that many workers receive packets, so expect a fraction of the forwarding this CPU could do.
# interfaces start at: 1500, this box's default. 'mtu <n>' under
```

With a card whose maximum is 1400, its line appears and the paragraph starts at the card's maximum:

```
# NIC max MTU, enp1s0: 1400, below the box's default of 1500
```

followed by a paragraph that starts with `# interfaces start at: 1400` and says why that value applies to all cards.

**A box installed before 0.152.1 stays at 9000.** The upgrade writes `mtu 9000` on every interface in the saved configuration that had no `mtu` and leaves the file `/etc/prisma-routing/mtu-9000-kept`; with it, the boot tuning writes no `default-mtu` at all, as before, and the report says:

```
# interfaces start at: VPP's 9000, kept on a box installed before
```

**The limit is stated, not applied silently.** One queue with seven workers means one worker receiving packets, and that is a fraction of the capacity that processor would have — it is sizing information, and hiding the cap would trade a box that does not come up for a slow box with no explanation.

A card with queues to spare gets a short line, and a card that handles the default does **not** get any MTU line:

```
# NIC queues, enp1s0: 8, enough for 7 worker(s)
```

And when the box cannot read the card's queues, it **says so**, because that is exactly the state the defect lived in:

```
# NIC queues, enp1s0: could not be read, so this asks for 7, one per worker. A card with fewer will refuse the device.
```

In that case it goes back to asking for one queue per worker, which is the number that used to fail. A box about to refuse its own card cannot look the same as a healthy box.

A box **without** a dataplane card says nothing about queues: a queue count is not a fact about a machine that has no card to count.

## IOMMU: with or without, and what changes

With an IOMMU in the firmware, the card goes to `vfio-pci`, which passes the device through the IOMMU - a defect in the dataplane cannot write to memory the device was never given. Without an IOMMU, `vfio-pci` loads and refuses every device, so the box uses `uio_pci_generic`, which **does not have that protection**, and **says on screen** that it is using it:

```
bind: no IOMMU groups on this box, so vfio-pci cannot take a device.
      Using uio_pci_generic, which has no IOMMU protection.
```

The choice is made where it can be explained, rather than discovered as a bind that fails for no apparent reason. Enable the IOMMU in the firmware (`VT-d` on Intel, `AMD-Vi` on AMD) if the machine has one: it is a firmware option, not a product setting.

### When handing over the card fails, the advice is for **your** box

If the card does not accept the driver, the box says what state it was left in — and the worst state of all, a card with no driver at all, is spelled out:

```
bind: 0000:5e:00.0 did NOT bind to vfio-pci; it is on no driver at all now.
      The dataplane will start without it. 'dmesg' says why vfio-pci
      refused the device.
```

Then comes the advice, and it is **different on the two boxes**. On a box with an IOMMU:

```
      This box has IOMMU groups, so the kernel command line is not the
      cause: vfio-pci is being offered a device it will not take.
      Read this, on the box:
        /sys/bus/pci/devices/0000:5e:00.0/iommu_group
      A device with no group of its own cannot go, and a group whose
      other members are still held by host drivers cannot either.
```

And on a box without an IOMMU:

```
      This box has no IOMMU groups, which is why vfio-pci was not
      tried. uio_pci_generic needs a legacy INTx interrupt, and a
      device that has only MSI-X is refused. Enabling an IOMMU in the
      firmware and on the kernel command line is what makes vfio-pci
      available instead.
```

For a while both boxes got the same sentence — *"on a box with an IOMMU the cause is usually* `*intel_iommu=on*` *missing from the command line"* —, and it was false on both, in opposite directions. On the box with an IOMMU, the parameter **was already** there, and the operator was sent to check a command line that was correct, in the only message they get when the appliance comes up without forwarding. On the box without an IOMMU the sentence was talking about another machine, and the parameter it cites would not have helped: that driver uses no IOMMU at all.

# The kernel, and the hardware it recognizes

The box's kernel is Prisma Routing's: the same code and the same configuration as the Debian kernel, recompiled, and `uname -r` ends in `-prisma`. Left **out** are the module classes a router does not use: sound, Wi-Fi, Bluetooth, NFC, video capture, joystick, touchscreen, amateur radio, CAN, 802.15.4, cellular modem, FireWire, industrial sensors, dedicated AMD and NVIDIA graphics cards and the experimental drivers (*staging*).

**Every network card, every disk controller and every virtualization driver,** `**vfio**` **and** `**uio**` **remain.** A machine whose network and disk hardware Debian 12 recognizes is recognized here. A new kernel, including a security fix for it, arrives **on the installation medium** of a new version, not through `apt` and not through the upgrade package: the package replaces the product's files and does not bring a kernel. On a box already in service, that means reinstalling from the medium, with what [Taking the box to a new version](/s/wiki-en/doc/taking-the-box-to-a-new-version-spJOdnrvOD) says about what the reinstall erases.

# If the box will receive the full BGP table

6 GB is the floor for a box that does **not** carry a full table. Receiving a full IPv4 table from a transit, routing alone comes to take about **1.4 GB** of RAM. Measured with one session and one million prefixes: `bgpd` ended up with \~840 MB and `zebra` with \~550 MB, against 37 MB combined on a box that only receives a default route. Each additional transit with a full table adds again, and IPv6 adds on top.

| Peering scenario | RAM for routing alone | Recommended machine floor |
|------------------|----------------------:|--------------------------:|
| Default route only | \~40 MB               | 6 GB                      |
| One full IPv4 table | \~1.4 GB              | 16 GB                     |
| Two transits with full tables, plus IPv6 | more than that        | 32 GB                     |

The dataplane reserves most of the RAM in hugepages and, without tuning, leaves `máx(2, RAM/4)` GB for everything else; `system memory control-plane <GB>` changes that number (section *Memory by role*). On a 6 GB box that is 2 GB, and a full table alone already takes almost all of it — that is why 8 GB is already tight and 16 GB is the sensible number.


:::info
**There is no memory limit configured for routing, and that is deliberate.** A tight limit does not make BGP slower: it kills `bgpd` in the middle of convergence, the service restarts five seconds later, converges again and dies again. What protects the box is the other side — the dataplane is outside the OOM killer's choice, so that, when memory runs out, what the kernel picks is the routing process itself that grew, and the box keeps forwarding with what it has already learned. The boot self-test says on screen whether that protection is in effect.

:::

# How many cores, and what is left for the control plane

The box hands the dataplane **every physical core of the processor serving the network card, except the first**. Those cores are isolated: the Linux scheduler puts nothing else on them, and each one runs a forwarding thread that never sleeps. The first core stays with the operating system, routing, the VTY, SSH - and the dataplane's main thread.

On a 4- or 8-core box this is balanced. On a 32-core processor, it is not: 31 cores forwarding and **one** carrying everything that does not forward. And the dataplane's main thread, which is on that same core, needs to stop forwarding for an instant once per second to do session maintenance. The more control work competes for that core, the longer it waits to be scheduled - and forwarding waits with it.

| Processor cores | Forwarding | For everything else |
|----------------:|-----------:|--------------------:|
| 4               | 3          | 1                   |
| 8               | 7          | 1                   |
| 32              | 31         | 1                   |

## Giving cores back

`workers <n>`, under `system` > `cpu` in the VTY (from 0.146.1; before that, only by editing `PRISMA_MAX_WORKERS` in `/etc/prisma-routing/tune.env`), limits how many physical cores become forwarding threads. It **comes empty**, and empty means no limit - exactly the behavior above. When it is set, the freed cores stop being isolated and go back to the scheduler, where routing and the VTY can actually use them.

Each core given back is that slice of forwarding capacity. There is no default value because the trade-off depends on how much control work the box carries: a box that only receives a default route has almost no control plane; one that receives two full tables, recomputes BGP policy and also serves queries has plenty.

**Lowering the number of workers costs one boot; raising it costs two, and the second is different.** Measured:

| What you do | Next boot | The boot after |
|-------------|-----------|----------------|
| Lower the limit (fewer workers) | already comes up with fewer workers | the freed cores leave isolation and go back to the scheduler |
| Raise the limit, or clear the value | comes up **in conservative mode**: no workers, no hugepages, no data card | comes up with the full plan |

The conservative mode in the second case is not a defect: the box cannot pin a worker to a core the kernel has not yet isolated, and isolating is a kernel command line matter, which only takes effect on the next boot. But it is a box without forwarding for one boot. **Schedule this change for a window**, and check the first boot's self-test before calling it done.


:::info
**The boot self-test says which side your box is on.** It prints how many cores were left for the control plane and warns when **one** was left while more than one is isolated - that is, when there was a core to give back and it was not given back. A two-core box does not get this warning: it has nothing to give back.

:::


:::warning
**The control plane is held to the cores that are not the dataplane's.** At every boot, the box holds the system, routing, SSH, the VTY and the control containers to all the cores the kernel did not isolate, and the dataplane runs in a separate group, with the workers on its cores. Before, this was only inheritance: measured on a 4-core box with 1 to 3 isolated, routing and SSH could run on any of the four, and a shell that asked for core 3 got it. Now the same request is refused, and `show system cpu` shows which cores the control plane is held to.

What decides how much CPU the control plane has is the division of cores by role, right below, and that is a sizing decision, not a process limit.

:::

## Cores by role: control plane, main thread and workers

The physical cores of the processor serving the card are distributed in this order: first those of the **control plane**, then - if requested - one just for the **dataplane main thread**, and the rest become **workers**. This is configured in the VTY, under `system` > `cpu`:

```
prisma# [edit] system
prisma# [edit system] cpu
prisma# [edit system / cpu] control-plane 2
prisma# [edit system / cpu] main-thread dedicated
prisma# [edit system / cpu] commit
```

| Command | What it does | Default |
|---------|--------------|---------|
| `control-plane <n>` | how many physical cores stay with the system, routing, SSH, the VTY and the containers | `1`     |
| `main-thread shared` | the dataplane main thread shares the first control plane core | yes     |
| `main-thread dedicated` | the main thread gets a core of its own, isolated like a worker's; costs one worker | no      |
| `workers <n>` | at most `n` workers; the leftover cores stay with Linux, not isolated | one worker per remaining core |
| `cgnat-workers <n>` | how many workers translate; the others only receive from the card and hand off to the worker that owns the subscriber | all do both |
| `no control-plane`, `no main-thread`, `no workers`, `no cgnat-workers` | back to the default |         |

**Why give the main thread a core:** once per second it stops the workers to expire sessions, and forwarding waits while it is not running. Sharing a core with routing, it waits for routing - with a full BGP table converging, that shows. On a core of its own, it waits for no one.

**RX workers and CGNAT workers.** By default, every worker receives packets from the card and also translates. With `cgnat-workers <n>`, the last `n` workers do only translation, and the others only receive and hand each packet to the worker that owns that subscriber. This costs one handoff between workers per packet; whether it pays off depends on the traffic and the machine, and it is exactly what a benchmark measures by comparing the two ways. Measured in a lab with three workers and worker 0 only receiving: every packet came in through worker 0, translation happened only on workers 1 and 2, and the dynamic pool was split between those two.

Asking for as many CGNAT workers as the box has separates nothing: at least one must receive, and the box says so in the boot report.

**The worker comes first.** On a box too small for what was requested, the box keeps at least one worker, gives up the dedicated core first and then control plane cores, and says so in the boot report with a `WARNING` line.

The change goes to `tune.env` (`PRISMA_CONTROL_PLANE_CORES`, `PRISMA_MAIN_THREAD` and `PRISMA_CGNAT_WORKERS`) and **takes effect on the next boot**, like everything that is core isolation - and with the same rule as the previous section: taking cores away from the control plane isolates cores that were not isolated, and that goes through one boot in conservative mode before the full plan. `show system cpu` shows side by side what is configured and what this boot is running:

```
prisma> show system cpu
configured, in effect from the next reboot:
  control-plane cores   2
  VPP main thread       dedicated
  VPP workers at most   every core left
  CGNAT workers         all, each also receiving
this boot:
  VPP main thread       core 0
  VPP workers           1-3
  translating workers   all of them
  isolated from Linux   1-3
  control plane held to 0
```

`show system tuning advice` says whether the running layout is the one the box should have — uneven workers, queues that do not match, main thread to spare or missing — with the VTY line that changes it, and brings a table from 4 to 32 vCPUs with the recommended split and the workers the box's tuning takes from each. On a VM, it also brings the CPU pinning for the hypervisor: see [Running Prisma Routing in a virtual machine](/s/wiki-en/doc/running-prisma-routing-in-a-virtual-machine-B5aywLiukm).

## Memory by role: control plane, CGNAT and buffers

The box's RAM is split into two sides. The **hugepages** belong to the dataplane: the CGNAT memory (sessions and subscribers), the packet buffers and some headroom. The **rest** belongs to the control plane: the system, routing, SSH, the VTY and the containers. A hugepage is never given back to anyone, so what the control plane is guaranteed is exactly the RAM that did not become a hugepage.

The three numbers are configured in the VTY:

```
prisma# [edit] system
prisma# [edit system] memory
prisma# [edit system / memory] control-plane 12
prisma# [edit system / memory] buffers 65536
prisma# [edit system / memory] commit
```

| Command | What it does | Without it |
|---------|--------------|------------|
| `system memory control-plane <GB>` | the RAM the control plane keeps, outside the hugepages | a quarter of the RAM, at least 2 GB |
| `system memory buffers <n>` | how many packet buffers per NUMA node | 16384 per worker |
| `nat cgnat memory <GB>` | the memory for CGNAT sessions and subscribers | calculated from the subscriber target |

A full BGP table takes about 840 MB in bgpd and about 550 MB in zebra, measured. A box that receives two full tables and also serves queries wants more than the default on a small machine. If the requested sum exceeds what the RAM allows, the boot limits the hugepages and says so in the report.

It takes effect on the next boot, because hugepages are reserved on the kernel command line. `show system memory` shows side by side what is configured and what this boot has:

```
prisma> show system memory
configured, in effect from the next reboot:
  control plane keeps   12 GB
  CGNAT memory          8 GB
  packet buffers        65536 per NUMA node
this boot:
  RAM                   16.0 GB
  hugepages             4.0 GB
  VPP main heap         2G
  packet buffers        49152 per NUMA node
  statistics segment    VPP default, 32M
  control plane uses    3.0 GB
```

## What the tuning file accepts

`/etc/prisma-routing/tune.env` is read at every boot, and each of its values has a form. A value outside that form is **refused out loud**, with the variable name and the value in quotes, and the box uses the factory default instead.

| Variable | Accepts | Factory default | If refused |
|----------|---------|-----------------|------------|
| `PRISMA_DATAPLANE_IF` | interface name, or empty | empty           | —          |
| `PRISMA_SUBSCRIBERS` | digits only | `200000`        | `200000`   |
| `PRISMA_LOW_LATENCY` | `1` or `0`, nothing else | `1`             | `1`        |
| `PRISMA_MAX_WORKERS` | digits only, or empty for no limit | empty           | no limit   |
| `PRISMA_CGNAT_MEMORY_GB` | whole GB, or empty for the estimate from `PRISMA_SUBSCRIBERS` | empty           | the estimate |
| `PRISMA_CONTROL_PLANE_CORES` | digits only, 1 or more, or empty | empty (one core) | one core   |
| `PRISMA_MAIN_THREAD` | `shared` or `dedicated`, or empty | empty (`shared`) | `shared`   |
| `PRISMA_CGNAT_WORKERS` | digits only, 1 or more, or empty | empty (all)     | all do both |
| `PRISMA_CONTROL_PLANE_MEMORY_GB` | whole GB, 1 or more, or empty | empty (a quarter of the RAM) | a quarter of the RAM |
| `PRISMA_BUFFERS_PER_NUMA` | digits only, or empty | empty (16384 per worker) | 16384 per worker |

`PRISMA_LOW_LATENCY=1` disables the processor's deep power-saving states, disables transparent huge pages and keeps the frequency governor at `performance`. It costs power and buys predictable bursts, and that is why it comes as `1`: this is a router. `0` leaves all three alone, for a box that needs to save power more than it needs to forward.


:::warning
**Write exactly** `**1**` **or** `**0**`**.** A value like `true` or `sim` is refused: the box says which variable it refused, with what value, and which two values it accepts and what each one does. It continues with `1` and the boot goes on.

This is deliberate: a typo must **not** cost the box its tuning. An ignored setting you fix by editing one line; a boot that fails in the middle of tuning leaves the kernel command line saying one thing and the dataplane file saying another, and then the box forwards halfway with no counter to show for it.

:::

**Changed** `**tune.env**`**? It only takes effect on the next boot.** Core isolation, hugepages and power states are kernel command line. When the file already says one thing and the box is still running the old line, the boot self-test says so in so many words, instead of reporting the box as tuned.

**And it reads what the kernel did, not what was requested.** A parameter can get lost in three places — `update-grub` did not run, the box did not reboot, or the kernel saw it and refused it —, and the box that asked for 1 GB pages on a processor that has no 1 GB pages spent its whole life saying it was tuned. There are sixteen rules read back from the running machine, each in one of four states: **in effect**, **requested and not in effect**, **cannot be read on this box** and **not requested here**. All four are described, with the lines the box prints, in *Operation and diagnostics*.

## The self-test checks the cores the dataplane received

The box computes the core plan at every boot and writes it to a file the dataplane reads on start. Two things can drift out of place with no symptom at all:

* the dataplane may have been running since **before** the last plan change, in which case it stays on the cores it came up with while the screen reports the new plan;
* the kernel command line may not have kept up, in which case a forwarding thread shares its core with everything else.

In both cases the box boots, forwards, responds to everything and delivers a fraction of its capacity. **There is no counter that gives this away**: a thread that never sleeps shows 100% usage whether or not it has the core to itself.

That is why the self-test does not check the file: it checks where the kernel actually placed the dataplane threads and which cores the kernel is actually isolating, and complains when the three do not agree. Restarting the dataplane fixes the first case; the second calls for an `update-grub` and a reboot.

# How the box calculates the dataplane memory

At every boot, `prisma-routing-tune` decides how many 1 GB pages (hugepages) the kernel reserves for VPP. This is memory taken from Linux and dedicated to the dataplane. Inside it lives the **main heap**, which is the CGNAT memory: all the sessions, the two session tables and the subscriber records live there.

## Giving CGNAT memory of its own

```
prisma> configure
prisma# [edit] nat
prisma# [edit nat] cgnat
prisma# [edit nat / cgnat] memory 8
prisma# [edit nat / cgnat] exit
prisma# [edit nat] exit
prisma# [edit] commit
```

The `commit` says how many sessions that number holds and that it **takes effect from the next reboot**: the memory is reserved through the kernel command line, and until then the dataplane has what it had at boot. Run `save` and reboot in your window. `no memory` goes back to the estimate.

**How to choose the number.** What decides is the number of **sessions** open at the same time, not the number of subscribers. Measured: about 300 bytes per session with millions of them, and less than 1 KB per subscriber. 200 thousand subscribers with 100 sessions each is 20 million sessions, about **6 GB**. With 300 sessions each, about 17 GB.

`show cgnat memory` shows the heap size, how much is in use, how many more sessions fit, how many were refused for lack of memory and whether the number given is already in effect.

## The math

**1. The CGNAT memory.** The number you gave. Without it, an estimate: `PRISMA_SUBSCRIBERS` × 13.5 KB, rounded to GB, at least 1. With the default of 200000 that is **3 GB**, about 45 sessions per subscriber.

**2. Packet buffers.** 1 GB for every 4 workers, rounded up, at least 1. On an 8-core machine, 1 stays with the system and 7 become workers: **2 GB**.

**3. Headroom.** **1 GB**, fixed, for the rest of VPP's physical memory.

**4. The desired amount** is the sum. With 8 GB given: 8 + 2 + 1 = **11 GB**.

**5. The cap from the machine's RAM.** Linux, Docker and FRR stay outside the hugepages, with `máx(2, RAM ÷ 4)` GB. RAM counts in whole GB, rounded down: a "16 GB" VM shows about 15.6, which becomes 15. So 3 GB are left for the system, the cap is 12, and the box reserves **min(11, 12) = 11 GB**.

**6. The heap** is what was reserved, minus the buffers, minus the headroom: 11 − 2 − 1 = **8 GB**, exactly what was given. **There is no longer a 2 GB cap**: that cap held any box at about 10 million sessions, whatever the RAM.

| VM RAM | CGNAT memory requested | Reserved | CGNAT heap | Sessions, about |
|--------|------------------------|----------|------------|-----------------|
| 8 GB   | estimate (3 GB)        | 5 GB     | 2 GB       | 6 million       |
| 16 GB  | 8 GB                   | 11 GB    | 8 GB       | 27 million      |
| 32 GB  | 16 GB                  | 19 GB    | 16 GB      | 56 million      |

When the cap cuts the desired amount, the tuning log warns how many GB were missing. Below 2 GB the box reserves nothing and boots in the conservative profile, which works but has no performance.

**When the heap fills up, the box refuses the new session and counts it**, and the dataplane stays up: the sessions that already exist keep translating.

# Memory per structure

| Structure | Sizing | Order of magnitude |
|-----------|--------|--------------------|
| Port bitmap | `block-size` bits per active subscriber | 64 bytes with 512-port blocks → \~13 MB for 200 thousand subscribers |
| Sessions  | one pool and two tables per worker | \~300 bytes per session, measured with 2 million sessions |
| Block ownership (dynamic mode) | 4 bytes per block, per worker slice |                    |

The session table is tuned with:

```
nat
 cgnat
  session-table buckets <n> memory <MB>
  exit
 exit
```

The default is 16 thousand buckets. **The** `**memory**` **number sizes nothing:** the tables, like all sessions, live in the dataplane's main heap, and that heap is what fills up.

**What decides memory is the number of sessions, not subscribers.** Measured: a subscriber costs less than 1 KB, and a session about 300 bytes when there are millions. 200 thousand subscribers with 100 sessions each is 20 million sessions, about 6 GB.

When the heap fills up, the box **refuses the new session and counts it**, and the dataplane stays up: the sessions that already exist keep translating. The counter shows up as `no memory for a new session`. Before this the whole dataplane went down, taking every subscriber with it.

# The counters segment, which brings down the dataplane when it fills

The dataplane publishes all its counters in a memory segment of **fixed** size. Each interface it carries takes a share of that segment — about 2.4 kB, plus 200 bytes per dataplane thread —, and the segment does not grow.

When it fills up, the dataplane **does not refuse the next interface: it aborts**. The box loses all forwarding, at once, and writes no explanation before going down.

The factory size is the dataplane's own: **32 MB**. How much fits in it depends on the number of threads, because each thread adds counters to every interface. Measured with VLAN subinterfaces: a single-thread box aborted after **7,800**, a three-thread box after **5,100**, and an eight-thread box — what an eight-core machine gets — after **3,300**. With the segment at 1 GB, a box reached 40,000 interfaces, which is where the measurement stopped and not where it hit a limit.

**An ordinary CGNAT box does not come close to this.** A subscriber is not an interface: 200 thousand subscribers behind a pool share a handful of ports. Who comes close is whoever creates **one interface per customer** - one QinQ subinterface per subscriber, for example, or a PPPoE session, which is an interface too.


:::info
**The box sizes this segment by itself, from what its configuration makes the dataplane carry.** At boot it counts the interfaces the configuration file names, the Linux side of each pair the dataplane keeps with Linux, and the maximum of PPPoE sessions the configuration allows, and reserves for each one **three times what its counters weigh** on the threads the box is going to run: about 7.9 kB on one thread, 12.1 kB on eight. Three times because growing the counters table needs the old copy and the new one at the same time, and the measured abort reached 2.5 times the weight.

If those interfaces fit in the factory 32 MB, it **writes nothing** - reserving less than what the dataplane already gives for free would only take counters away from your box. And it never gives the segment more than a quarter of the memory the control plane keeps: a configuration that would ask for more gets that ceiling, and the boot tuning says how many interfaces it holds.

:::

Apart from PPPoE sessions, whose maximum the configuration declares, what no configuration file has is the interface created **while the box is running**. If your topology creates interfaces at runtime, the number is yours and is in the next section.

**The new size only takes effect on the next dataplane start.** The segment is read when it comes up and never grows afterwards: if you create thousands of subinterfaces on a box that is already running, they consume the segment that box came up with. The calculation goes into the dataplane configuration file on the next boot, and takes effect from there.

The boot self-test prints how much of the segment is used, over how many interfaces, and **who chose the size**:

```
  [ ok ] statistics segment: 4.20M of 31.99M used, 3 interface(s)
        size: VPP's own default, which this product does not set
```

There are three possible answers on that last line: the dataplane default, `chosen by prisma-routing-tune from this box's configuration` when the box calculated it, and `set in /etc/prisma-routing/vpp-base.conf` when it was you.

Past half, this becomes a warning. Past three quarters it becomes a **failure**, with what happens when it fills up and what to do about it. Three quarters, and not ninety percent, because the last piece is not usable: the counters table is a single block of memory, and growing means copying it whole, which requires the old copy and the new one at the same time.

## How to choose the size yourself

In `/etc/prisma-routing/vpp-base.conf`, which is the base file you edit:

```
statseg {
  size 256M
}
```

Reboot the box afterwards. The automatic boot tuning **never overwrites** this section: it appends the blocks it calculates **after** what is in this file, and when a `statseg` of yours exists it writes none - the number becomes entirely yours, even if it is smaller than its own calculation would ask for.


:::warning
**This segment is RAM taken from everything else.** Do not ask for 1 GB on a 6 GB box as a precaution. Ask for what your topology's number of interfaces calls for, and check in the next boot's self-test how much of it is actually being used.

:::

# How many public addresses

In **deterministic mode** every subscriber occupies its block whether online or not. The math is direct:

```
public addresses = subscribers × block-size ÷ 64512
```

(64512 is the usable port range, from 1024 to 65535.)

| Subscribers | Block | Addresses | Prefix |
|------------:|------:|----------:|--------|
| 50,000      | 512   | \~397     | `/23`  |
| 100,000     | 512   | \~794     | `/22`  |
| 200,000     | 512   | \~1,588   | `/21`  |
| 200,000     | 256   | \~794     | `/22`  |
| 200,000     | 1024  | \~3,175   | `/20`  |

**Dynamic mode** allocates only for those who are using it, so oversubscription fits and the need drops considerably — in exchange for generating logs again.

## In dynamic mode, size by the number the box guarantees

On a box with more than one worker, the blocks of a dynamic pool are **split among the workers**: each block belongs to a single worker, and each subscriber goes out through a single worker. The two splits are calculated in different ways, so they do not always match - a subscriber can get "no free block" while there are blocks left in another worker's slice.

That is why `show cgnat pool` shows **two** numbers for a dynamic pool:

```
capacity     768 subscribers (usable 768)
             384 whatever the addresses are: blocks are sharded 2 ways and a subscriber draws only from its own worker's slice
```

* the first is the **ceiling**: what the pool serves if subscribers are spread perfectly across the workers;
* the second is the **guaranteed floor**: what the pool serves regardless of which internal addresses are online.

**Size by the second.** The first depends on luck in the addressing plan, and luck is not a number you can buy against.

The second line only appears when there is something to lose. A pool with blocks to spare for every internal address loses nothing to the split, and in that case the box prints only the ceiling.

The gap between the two shrinks quickly as the pool grows relative to the number of workers. Measured, with subscribers spread randomly across the internal range:

| Blocks in the pool | Workers | Guaranteed | Served in practice | Loss |
|-------------------:|--------:|-----------:|-------------------:|-----:|
| 12                 | 2       | 6          | \~10.6             | 11%  |
| 48                 | 4       | 12         | \~43.3             | 10%  |
| 192                | 4       | 48         | \~183              | 4\.5% |
| 768                | 8       | 96         | \~742              | 3\.4% |
| 7,936              | 8       | 992        | \~7,848            | 1\.1% |

And the loss **disappears** when internal addresses are handed out in sequence from the start of the range, which is the case of a DHCP pool filling from the bottom up. A contiguous addressing plan loses nothing; a patchy plan loses what the rightmost column says.


:::warning
The practical conclusion: in dynamic mode, **a small pool with many workers is the worst case**. A 12-block pool with 8 workers guarantees a single subscriber. If the pool is small by choice - and in dynamic mode it usually is, since it exists precisely to save IPv4 - prefer fewer workers or a larger pool over finding this out with the customer on the line.

:::


:::info
A smaller block saves addresses and squeezes the subscriber. 512 ports serve ordinary residential use with room to spare; below 256, applications that open many simultaneous connections (a browser with many tabs, a torrent client) start hitting the limit. Watch with `show cgnat top` before reducing.

:::

# Log volume

Explicit assumptions, for order of magnitude and not precision: 200 thousand subscribers, dynamic mode, a block renegotiated \~5 times per day per active subscriber, \~40 bytes per record with IPFIX framing.

| Scenario | Records/day | Raw volume/day | One year |
|----------|------------:|---------------:|---------:|
| BPA (allocation + release) | \~2 million | \~80 MB        | tens of GB |
| Per-flow NAT (reference) | \~40 million | \~1.6 GB       | tens of TB |
| Deterministic | 0           | 0              | 0        |

## The records have their own filesystem

The installer creates three partitions — ESP, root and `/var/log/prisma-routing` — instead of putting everything together. What lives on the third volume is the **configuration trail** and the exporter's **spill**: this box does not store connection records, it exports them, and what keeps them for the year the Marco Civil (Brazil's Law 12.965/2014; retention requirements differ by country) requires is the collector.


:::warning
Sharing the filesystem with everything else, a log that fills up prevents the machine from writing its own configuration and its own trail, because it is busy recording who connected to what. With its own volume, a full log is just a full log.

:::

The default size is arithmetic and the math appears on screen: \~80 MB per day for 200 thousand subscribers in dynamic mode, or about **29 GB per year**. Deterministic mode records no allocation at all and needs almost no space — which is why this is a question and not a constant. The root never gets less than 8 GB.

In an unattended install, `prisma-routing.log-gb=` answers this question on the kernel command line.

The volume is mounted with `nofail`: a machine that does not boot because the records volume disappeared is a machine nobody can get into to find out why. That is why the boot self-test **reports where the records are** — without it, a box whose volume did not mount comes up quietly writing to the root.

This table is the business case for block-based CGNAT in one line: keeping a year of records goes from tens of terabytes to tens of gigabytes — or to nothing. It is an argument about the **collector's** disk, not this box's.

# How to check

After installing, on the console:

* the self-test reports RAM, instruction set, VPP, restart counters, tuning state, FRR and configured pools;
* it also checks **the cores**: whether the dataplane is on the cores its plan names and whether the kernel is isolating those cores, and says how many were left for the control plane;
* it **distinguishes warning from failure** and always exits with code zero, because a diagnostic must not bring down the boot;
* the restart counter matters more than it seems: a looping service can be "active" at the instant you look, and only `NRestarts` tells the story.


:::success
Self-test saying ready, with no missing-instruction warning and no restart counter climbing: the machine is sized for what you asked of it.

:::

# See also

* [Getting started](/s/wiki-en/doc/getting-started-WksYlCtcGd) — the configuration once the hardware is right
* [Pool configuration](/s/wiki-en/doc/pool-configuration-wkJ1DcmG2Y) — where `block-size` and the mode are chosen
* [Expanding capacity without dropping subscribers](/s/wiki-en/doc/expanding-capacity-without-dropping-subscribers-R1zYvsfLCT) — when the math above changes
