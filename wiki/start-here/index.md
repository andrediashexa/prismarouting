# Start here

Everything you need to go from a machine with no system to a CGNAT translating traffic. Read in this order if this is your first time with the product.

| Page | Answers |
|------|---------|
| [Sizing and requirements](/s/wiki-en/doc/sizing-and-requirements-IHPnUSMSom) | How much machine, how many public IPs and how much log your case requires |
| [Running Prisma Routing in a virtual machine](/s/wiki-en/doc/running-prisma-routing-in-a-virtual-machine-B5aywLiukm) | Memory, CPU, virtio cards, 1 GB pages, shutting down from the VTY and what a measurement in a VM actually measures |
| [Appliance installation](/s/wiki-en/doc/appliance-installation-gliHrNahhB) | How the ISO installs, what it asks and how to automate it |
| [Access, login and roles](/s/wiki-en/doc/access-login-and-roles-uVm2WUy5xB) | Where you land when you log in, and who can change what |
| [Getting started](/s/wiki-en/doc/getting-started-WksYlCtcGd) | From an empty disk to subscribers browsing, in ten steps |
| [Configure CGNAT step by step](/s/wiki-en/doc/configure-cgnat-step-by-step-jU9hEJYMch) | From the two ports to the first translated subscriber, with the screen the box shows at each step |


:::info
**Before installing, decide whether the box will be managed over the network.** A new box is configured on the console, and the dataplane takes every card it has. If it needs to be reached over `ssh` after the first reboot, answer the management card question during installation: it is the only one the dataplane does not claim. A single-card box hands that card to the dataplane anyway, and the management address goes with it.

:::

# See also

* [Day-to-day recipes](/s/wiki-en/doc/day-to-day-recipes-KCc5cOuBip) — once you are up and running
* [Taking the box to a new version](/s/wiki-en/doc/taking-the-box-to-a-new-version-spJOdnrvOD) — `system upgrade`, and what to do before the next reboot
* [Deterministic and BPA — concepts and mapping](/s/wiki-en/doc/deterministic-and-bpa-concepts-and-mapping-NMOHVImQiU) — why the mapping is a calculation and not a table
