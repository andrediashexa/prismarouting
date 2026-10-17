# Running Prisma Routing in a virtual machine

What a virtual machine needs to have for the box to forward, and what changes in it compared to a physical server. For anyone who is going to evaluate the product on a hypervisor, or put it into production on one.

# Memory and processor

| Resource | What to ask for | What happens if it is missing |
|----------|-----------------|-------------------------------|
| RAM      | **6 GB minimum, 8 GB recommended** | with 4 GB the box comes up in the conservative profile, without hugepages, and the dataplane receives no card at all: `show interface` only shows `local0` and nothing is forwarded |
| Processor instructions | **SSE4.2** (AVX2 is used when present) | without SSE4.2 the dataplane dies at startup, in a loop; the boot self-test checks the processor **before** looking at the dataplane and says what is missing |
| 1 GB pages (`pdpe1gb`) | desirable, not mandatory | the box falls back to 2 MB pages on its own; see *Processor model without 1 GB pages* below |

Measured on a VM with 8 cores, after the second boot, which is the one that applies the tuning:

| VM RAM | What the box does |
|--------|-------------------|
| 4 GB   | comes up, does not forward |
| 6 GB   | forwards, with 1 GB of heap: about 2.7 million sessions fit |
| 8 GB   | forwards, with 2 GB of heap |


:::warning
**The first boot is not yet the final boot.** Hugepages and core isolation come from the kernel command line, so they only take effect from the next boot. A VM evaluated on the first boot is in the conservative profile and says nothing about what the box does.

:::

The processor model matters. QEMU's generic `qemu64` model does not have SSE4.2: give the VM the host's processor, or a model that has SSE4.2.

## Processor model without 1 GB pages

Many virtual processor models do not expose 1 GB pages — this is the case with the default model of a VM on Proxmox. The box reads this at boot and:

| What it adjusts | Why |
|-----------------|-----|
| reserves the hugepages in **2 MB** pages instead of 1 GB | asking a processor that does not have 1 GB pages for them reserves nothing, silently |
| keeps the number of pages it calculated, even against the tuning the dataplane package ships from the factory | that tuning, on a 2 MB box, would shrink the reservation after boot and the dataplane would not come up |
| addresses the card by physical address | on a 2 MB box it is the only mode in which the dataplane can use the card |

There is nothing to configure: since version 0.120 a VM without 1 GB pages comes up and forwards. Before that it came up without a dataplane.

# Virtual network cards

| Situation | What the box does |
|-----------|-------------------|
| **virtio** card | this is the common case and the most tested; handed to the dataplane like any PCI card |
| **legacy virtio** card (the one QEMU gives a VM without IOMMU) | also handed over, since version 0.121; before that the dataplane came up and did not take the port, without saying why |
| interface **without a PCI address** (bridge, bond, veth, `lo`) | cannot be handed to the dataplane; the box comes up, routes and does not forward, and the boot self-test says so |

## VM without IOMMU

Most VMs have no virtual IOMMU. The box notices this at boot and binds the card to the dataplane through the `uio_pci_generic` driver instead of `vfio-pci`.

|     | With IOMMU | Without IOMMU |
|-----|------------|---------------|
| driver used | `vfio-pci` | `uio_pci_generic` |
| what you gain or lose | the card only reaches the memory it was given, so a defect in the dataplane does not write outside it | forwards the same, without that protection |
| where to check | `show hardware` says, for each card not yet handed over, which driver the box will move it to | the same      |

## Card added with the VM running

A card added to the VM by the hypervisor while the box is running forwards **right away**, without restarting anything:

| Step | What happens |
|------|--------------|
| the card appears | the box only takes it if the boot rule would also take it; a card with an IPv4 address or with the default route stays with Linux |
| the box takes it | it enters the dataplane through **AF_XDP**, with the name Linux gave it and the card's own MAC |
| you configure it | like any other: `interface <name>`, address, `no shutdown`, `commit` |
| `show interface` | warns, below the count, that it was added with the box running and forwards through AF_XDP until the dataplane restarts |
| `system dataplane restart` | moves the card to the final mode (DPDK), in the window you choose. A reboot does the same |

AF_XDP costs speed, not correctness: the card is read by the kernel driver. On a single-queue virtio card, the kernel itself warns that transmit runs in a slower mode.


:::warning
`system dataplane restart` stops forwarding until the dataplane comes back and **discards the port block and sessions of every subscriber**. The dataplane comes back with the **saved** configuration: what was applied with `commit` and not saved does not come back. The screen states the cost before acting. Measured on a VM: 18 seconds without forwarding.

:::

## The MTU the virtual card carries

Every virtual card the hypervisor configures announces a maximum — usually 1500. The dataplane, on its own, starts every interface at 9000, and a card that does not accept that value refuses the port: it stays in error and forwards nothing.

| What the box does | Consequence |
|-------------------|-------------|
| reads at boot the maximum of each card it is going to take | the number comes from the hypervisor, not from an assumption |
| makes **all** interfaces start at the **lowest** maximum among the claimed cards | on a VM with a 1500 card next to a 9000 one, the 9000 one also starts at 1500 |
| accepts `mtu <n>` in the interface block afterwards | that is how you raise the port that carries more |
| writes nothing when all cards carry 9000 or more | it stays the same as a physical box |

To carry frames larger than 1500 on a VM, the MTU must first be raised on the virtual card, in the hypervisor; the box reads the new maximum on the next boot.

## Multiple queues on the virtual card (multiqueue)

The box asks the card how many queues it has, and only that number of workers receive packets from it. A virtual card with **one** queue on an eight-core VM makes one worker receive everything — the boot report says so on one line per card, with the limit, instead of leaving the box slow without explanation.

| Queues on the virtual card | Workers that receive packets |
|----------------------------|------------------------------|
| 1                          | 1, whatever the number of cores |
| equal to or greater than the number of workers | all                          |

To make use of the VM's cores, enable multiple queues on the virtual card in the hypervisor (the option is usually called *multiqueue*) with a number equal to the number of workers. The box rereads the queues on every boot.

# Shutting down the VM

The hypervisor's *Shutdown* sends the power button (ACPI), and the box shuts down cleanly: it closes the blocks in the retention log and stops the services before powering off. Measured on Proxmox: 41 seconds, within the time the hypervisor waits before forcing it.


:::warning
**Up to version 0.139.0 the box ignored this button.** On a box at that version or earlier, the hypervisor's *Shutdown* does nothing and the forced power-off that follows is the same as pulling the power cord: the blocks the dataplane is holding are not closed in the retention log. Upgrade the box, or shut down from the VTY.

:::

Shut down or reboot from the VTY, which states the cost before acting and closes the blocks along the way:

```
system shutdown
```

```
system reboot
```

# The box suggests its own tuning, and the hypervisor's

On a VM, each vCPU is a hypervisor thread, and a dataplane worker never lets go of its CPU. If two worker vCPUs land on the same physical core (two HT siblings), or if another VM shares the thread with one of them, that worker spins slower than the others, and the time the hypervisor takes from the VM shows up as *steal*. That is what happened on a lab box: with almost zero traffic, the five workers spun between 2.41 and 3.29 million loops per second, with 7.7% steal. Pinning each vCPU to a host thread, on the card's NUMA node, one physical core per worker, they went to 3.21–3.30 million, and the steal stayed only on the control plane.

The box says this on its own:

```
prisma> show system tuning advice
prisma> show system tuning advice seconds 10
```

It shows what it has (CPUs, whether it is a VM and whose, memory and hugepages, each dataplane card with its queues and the worker for each one, the layout, MTU and `tcp-mss`), measures a window of a few seconds (5 by default) without resetting any counter, and says what is wrong, always with the measured number, the limit and the why: uneven workers (a worker more than 15% below the median, with no traffic), steal above 1% on the dataplane CPUs, queues that do not match the workers (only on the cards the dataplane forwards through: the management card, in the management VRF, a card that is shut down or one without an address and without a CGNAT side show up as checked, with the reason, and do not ask for queues on the host or a dataplane restart), a worker outside the card's NUMA node, fewer hugepages than the box asked for, a dedicated main thread on an idle box or one shared with a busy control plane. Nothing is changed: applying it is up to you.

What to do comes in two parts. **On the box**, the exact VTY lines and whether each one requires a dataplane restart or a reboot. **On the hypervisor**, when it is a VM: the principle, a Proxmox *hookscript* (`/var/lib/vz/snippets/prisma-pin.sh`), the `qm set` lines and a libvirt `<cputune>` block, generated from the role of each vCPU of this box. The host CPU numbers come as `HOST_*` — the script refuses to run while any remain — unless you paste the host topology:

```
prisma> show system tuning advice host-topology
```

Paste the output of `lscpu -e=CPU,NODE,SOCKET,CORE` from the host and, on a separate line, `nic-node <n>` with the card's NUMA node (`cat /sys/bus/pci/devices/<pci>/numa_node`); finish with an empty line. The box then puts the main thread on the first physical core of the card's node, the control plane on that core's HT sibling (the main thread is almost always idle, so it is the quietest place on the node), each worker on a core of its own, the other QEMU threads on the last remaining siblings, and the other VMs on the other node.

Pinning is not enough to keep everything else away from those CPUs, so the **host kernel command line** comes too: `isolcpus`, `nohz_full` and `rcu_nocbs` on all the host CPUs the VM receives. With `isolcpus`, the host scheduler never puts another VM, a host service or a kernel thread there unless it is pinned there, so there is nothing else to configure; the other two remove the timer tick and the RCU callbacks from those cores. It requires rebooting the host and is static. (A cgroup cpuset partition does the same at runtime, but it is not managed by Proxmox.)

The same line reserves the VM's memory in 1 GB pages on the card's node (`hugepagesz=1G hugepages=<node>:<GB>`), and the `qm set` lines bind the memory there: `numa0: cpus=0-<n-1>,hostnodes=<node>,memory=<MB>,policy=bind` with `hugepages: 1024`. The dataplane buffers and the card's DMA live in that memory, and memory on the other socket costs latency and throughput on every packet; `numa: 1` alone binds nothing. On the lab box, the VM had 16.1 GB on node 1 and nothing on node 0 — local by first-touch luck, not by binding. Also paste the output of `numastat -p <VM qemu pid>` after the topology and the box says where the memory is now.

At the end comes a sizing table: for VMs from 4 to 32 vCPUs, how many go to the control plane, whether the main thread is dedicated, how many workers that gives — calculated by the box's own tuning — and how many host threads to reserve. It is the number for deciding how many vCPUs to give a new VM.

# Measuring performance on a VM

A throughput number measured on a VM measures the **hypervisor's virtual switch path**, not the box. The dataplane's cost is per packet, and on a VM what runs out first is the number of packets per second the hypervisor delivers to the virtual card.

| What is measured | What it tells you |
|------------------|-------------------|
| packets per second | the real limit of the path: it is what runs out first |
| bits per second with small frames | little: the same packet limit, with few bits in each |
| bits per second with large frames | more bits in the same number of packets — that is why a measurement with large frames looks better, without the box having changed |

The box on a VM serves to evaluate functionality, configuration and CGNAT behavior. Production throughput is measured on a physical card the dataplane accesses directly.

# See also

* [Sizing and requirements](/s/wiki-en/doc/sizing-and-requirements-IHPnUSMSom) — how much machine and how many addresses your case requires
* [Appliance installation](/s/wiki-en/doc/appliance-installation-gliHrNahhB) — the installation to disk, step by step
* [Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn) — `mtu`, addresses and the `interface` block
* [Operations and troubleshooting](/s/wiki-en/doc/operations-and-troubleshooting-kql3GmIqmX) — the boot self-test and what it says
