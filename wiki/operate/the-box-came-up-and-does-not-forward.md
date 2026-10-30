# The box came up and does not forward

The box powered on, answers on the VTY, accepts configuration — and passes no packets. This page is the diagnostic order for that case: what the boot self-test already said, the two screens that show what the dataplane has, where the reason is kept when the dataplane gave up, and how to get the box forwarding again. For whoever is at the console of a new or freshly restarted box.

A box with no dataplane interface looks like a working box. That is why the box itself tells you, in three places, what it decided and why — and the diagnosis is reading those three places in order.

# 1. Read the boot self-test

On every boot the box prints to the console (serial, IPMI or the virtual machine screen) a block that starts with `=============== Prisma Routing: boot self-test ==============`. Each line is `[ ok ]`, `[warn]`, `[FAIL]` or `[info]`, and failures carry the reason on the lines below. The ones that answer "why doesn't it forward":

| The console says | What it means | What to do |
|------------------|---------------|------------|
| `[warn] running in FIRST BOOT mode: no pinning and no hugepages.` | first boot after installation: the CPU and memory tuning only takes effect from the next boot, and until then the dataplane comes up conservatively, without DPDK | restart the box. This is expected, not a defect |
| `[warn] running between two tuning plans until the next reboot:` | a tuning change asked for more workers than the kernel isolated on this boot. The box forwards with the workers already isolated, and the two lines below say how many now and how many later | restart when you can to move to the new plan. Until then it forwards, with fewer cores |
| `[info] DPDK is off until hugepages exist; no NIC can be claimed yet` | the same case, seen from the card side | the same: restart |
| `[warn] no dataplane interface: this box has no PCI network card for the` … | the box has no card the dataplane can take | see step 3; a virtio card in a virtual machine counts, a card Linux does not see does not count |
| `[warn] no dataplane interface: this box has <n> card(s) and claimed` … | the box has cards and took none: the only candidate was the card through which it is reached, and it could not read its address, MAC and gateway to hand back to Linux | see step 3 and name the card (step 6) |
| `[FAIL] the dataplane has no interface, and one is named` | a card was handed to the dataplane and it does not have it; the lines below say which driver it is bound to | see step 5 |
| `[FAIL] the dataplane holds interface(s) its driver refused to start:` | the dataplane has the port, but the card's driver refused to start it; everything else looks right | see step 5 |
| `[FAIL] rx queues: none opened on <card>` | the same, seen through the queues: no receive queue was opened | see step 5 |
| `[warn] rx queues: <n> of <m> on <card>, <k> requested` | the box forwards, but the card opened fewer queues than the tuning asked for: only `<n>` workers receive from it | does not prevent forwarding; it is capacity, not a defect |
| `[warn] requested and NOT in effect: <rule>` | a tuning rule (hugepages, `isolcpus` and others) was requested and the kernel did not apply it; the line below says what to do | see step 5, *hugepages* |
| `[FAIL] VPP not responding` | the dataplane is not responding; the screen shows the last lines it wrote | see step 4 |
| `[FAIL] ... has restarted <n> times: it is looping` | the dataplane comes up and goes down in a loop | see step 4 |
| `[warn] a card this box has is not on the dataplane` | a card installed after boot was not taken | `system reboot` hands the card over; see step 3 |

On a box that forwards, the card line is `[ ok ]` and says which card the dataplane took and why — `claimed by this box with nobody asked` when the box chose on its own, or the configured name when someone named one.


:::info
**The box takes every PCI card it has, except one reserved for Linux, and never the last one.** The reserved one is the management network card given during installation. On a box with a single card, it takes that card and the management address goes along to the Linux side of the port.

:::

# 2. `show interface`

```
show interface
```

If the dataplane has no port besides its own internal one, the screen starts with `the dataplane has no interface to forward on:` followed by the reason, read from the same files the self-test reads:

| Reason | What to do |
|--------|------------|
| `DPDK is off until hugepages exist, so no NIC can be claimed yet.` | first boot: restart |
| `the dataplane is off on the installation medium, and a reboot does not change that: nothing here survives one.` | you are on the installation medium: `install` puts Prisma Routing on a disk |
| `no interface was named, so VPP was given no NIC.` | name the card (step 6) |
| `'<name>' was named and VPP was given no NIC.` | the named card does not exist, has no PCI address, or carries the default route and its return path could not be read; check the name in `show hardware` |
| `'<PCI address>' is named in the startup configuration and the dataplane does not have it.` | the card was handed over and the dataplane does not have it; the lines below say the driver (step 5) |

If the port shows up, look at its state: `error` is a port the card's driver refused to start (step 5). A card installed while the box was running shows up with the note that it forwards via AF_XDP until the dataplane restarts.

# 3. `show hardware`

```
show hardware
```

Lists the cards **as Linux has them**: name, PCI address, driver, link state and speed, with the MAC and what happens to each card when it is handed to the dataplane. The card carrying the default route is marked with `*** carries the DEFAULT ROUTE ***`.

Before the list, the screen says what the dataplane already has — a card the dataplane took no longer appears to Linux and, therefore, is not in the list — or repeats the reason from step 2 when it has nothing. At the end, it says which cards the box took on its own, which one it spared for Linux, and which one is waiting for the next boot.

This is where you check whether the card exists for Linux. A card that appears neither here nor in the dataplane does not exist for this box: check the virtual machine or the slot.

# 4. The dataplane gave up: the reason file

The dataplane runs in a container. If it cannot come up, the container is restarted a few times and then stops trying. At that point the card had already been taken from Linux to be handed to the dataplane, and the box would be left with no interface at all.

**A card watchdog takes care of that.** Every 5 seconds, starting two minutes after boot, it checks whether the dataplane container has stopped for good; if it has, it:


1. writes the reason to `/var/log/prisma-routing/why-the-dataplane-stopped` — the container state, the exit code and the last 80 lines the dataplane wrote;
2. returns **all** taken cards to the kernel driver;
3. brings the Linux interfaces up again, so the box becomes reachable again.

It gives the cards back once per boot. Before it gets to that, it handles two cases: a dataplane that crashed and that Docker started again gets the configuration in effect from the watchdog, a few seconds later; and a dataplane that exited by itself with status 0, with nobody asking, is started again, up to five times per boot. In practice, a box whose dataplane does not come up has the card back in Linux about two minutes after boot, without a dataplane.

**The file lives in** `**/var/log**`**, not in** `**/run**`**, because everybody's next step is to restart**, and the restart erases the container log. Read it before touching anything. With the dataplane stopped, `show version` points to it:

```
show version
```

The plugin line says the dataplane is not responding and ends with `the dataplane gave up and /var/log/prisma-routing/why-the-dataplane-stopped has what it printed before it did`. To read the file, an administrator account opens a shell with `start shell`:

```
start shell
cat /var/log/prisma-routing/why-the-dataplane-stopped
exit
```

The last lines are the ones that give the reason. The most common ones are in step 5.

# 5. The causes, and the fix for each

**Hugepages.** The dataplane needs memory reserved in large pages: 1 GB when the CPU offers that size, 2 MB when it does not — the case of many virtual machines with the hypervisor's default CPU model. The tuning picks the size on its own and writes the reservation for the next boot.

* On the first boot the reservation does not exist yet: restart (step 1).
* If the self-test says `requested and NOT in effect:` with a hugepages rule, the kernel did not make the requested reservation; the line below says what to do. On a virtual machine, check the memory given to the machine: 8 GB is the floor to use, and a 6 GB machine did not bring the dataplane up.
* If the reason file ends in `rte_eal_init failed: Cannot allocate memory (12)` or in `Invalid argument (22)` on a box with 2 MB pages, it is the defect fixed in versions 0.119 and 0.120: move the box to a current version.

**A card the driver refused.** The port shows up in `show interface` in the `error` state, and the self-test says `the dataplane holds interface(s) its driver refused to start:`. Everything else — driver, Linux pair, address — looks right, and the port passes no packets.

* The known cause is **MTU**: the box starts each port at the smallest maximum MTU among the cards it took; a port in `error` after a configured `mtu` is a value the card cannot carry. Lower the `mtu` of that interface.
* The driver's reason is in the dataplane journal; `show interface` and the self-test point to where.

**A card handed over and not taken.** The self-test says `[FAIL] the dataplane has no interface, and one is named`, and the next line says the situation:

| The next line says | What it means |
|--------------------|---------------|
| that it is on a driver DPDK uses | the handover was done; the reason is in the dataplane journal |
| that it is on a kernel driver | DPDK cannot take the card through it |
| that no driver is bound to it | the card was left without an owner |
| that no driver DPDK uses is loaded | neither `vfio-pci`, nor `uio_pci_generic`, nor `igb_uio` |

On a box that takes several cards, a card that could not be handed over is left out and the dataplane comes up with the others.

**An ixgbe card the kernel refused because of the SFP+ module.** The card does not appear in `show hardware`, and the boot journal (`journalctl -b | grep ixgbe`) says `unsupported SFP+ or QSFP module type was detected`. The driver refused the module, not the card. Turn on `unsupported-sfp allow` under `system` > `hardware` and restart the box; see [Routing, SSH, users and SNMP](/s/wiki-en/doc/routing-ssh-users-and-snmp-jjxcPssV72).

**The named card does not exist.** If `show interface` says the configured name received no card, check the name in `show hardware` and name it again (step 6).

**A card the saved configuration uses was not there at boot.** The box does not refuse the whole configuration for it: it applies everything else, leaves out what depends on that card (its block, its sub-interfaces, what names it in routing) and says so on the console. `show configuration pending` shows what is waiting. While anything is pending, `save` is refused, so the file does not lose those lines; when the card appears, the box applies what was missing by itself, and `clear configuration pending` is you giving it up.

# 6. Fix it, and restart the dataplane

To say which card the dataplane should take:

```
configure
 system
  dataplane
   interface <name>
 commit
```

A name the box does not have is refused, and the refusal lists the ones it has. The `commit` saves the choice and recalculates the tuning, but does **not** hand the card over: the screen says `NOT IN EFFECT YET` and explains what the handover will bring down.

After fixing the cause, make the dataplane come up again, in one of two ways:

```
system dataplane restart
```

or

```
system reboot
```

`system dataplane restart` returns all cards to Linux, recalculates the tuning, takes the cards again and brings the dataplane up with the **saved** configuration. A card the watchdog had returned is taken again here or on the next boot. Both ways require `root` or an account with `operator`.


:::warning
**Restarting the dataplane brings forwarding down until it comes back, and every subscriber loses their port block and open sessions;** blocks are allocated again as traffic returns. And the dataplane comes back with the **saved** configuration: run `save` first, or what was applied with `commit` and not saved does not come back.

:::

# How to verify

```
show interface
show hardware
```


:::success
`show interface` lists the card with the state `up`, and not the phrase `the dataplane has no interface to forward on`; `show hardware` says the dataplane has the card. On the next boot, the self-test shows `[ ok ]` on the card line, `[ ok ] every tuning rule this box can read back is in effect` and no `[FAIL]`.

:::

# See also

* [Appliance installation](/s/wiki-en/doc/appliance-installation-gliHrNahhB) — what the installation asks, including the management network that stays with Linux
* [Sizing and requirements](/s/wiki-en/doc/sizing-and-requirements-IHPnUSMSom) — memory, CPU and cards
* [Operations and troubleshooting](/s/wiki-en/doc/operations-and-troubleshooting-kql3GmIqmX) — where to start when the problem is not the card
* [Taking the box to a new version](/s/wiki-en/doc/taking-the-box-to-a-new-version-spJOdnrvOD) — when the fix is a current version
