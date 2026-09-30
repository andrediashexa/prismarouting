# Where this stands, for whoever picks it up next

Written 2026-09-28, at 0.140.0, so the next session starts with the state
rather than reconstructing it. **This file is a handoff, not a plan** —
`docs/roadmap.md` is the plan, and where the two disagree the roadmap is
right. `CLAUDE.md` is how this repository is worked on; read that first.

## The short version

**Nothing is on a branch waiting.** Every issue that was work somebody could
start has been released and closed. What is open is one of four things, and
none of them is waiting for somebody to start it:

* **a decision the maintainer has not taken**, with the evidence already on
  the issue - #485 (NAT64) is the new one;
* **a bench that needs the maintainer's say-so** to go further - #475;
* **blocked**, on hardware this project does not have or on an upstream;
* **deferred by its own issue** until there is demand for it.

## Released since the last handoff (0.138.0)

The CHANGELOG has each one. By theme:

| | | |
|---|---|---|
| 0.138.2 - 0.138.5 | a /0 prefix refused; a unit test for every CGNAT function; a whole-configuration load that drops a sub-interface reaches FRR; `exit` after a commit no longer warns about discarded changes | #473 #472 #474 #477 |
| 0.138.6 - 0.138.7 | the public name is prismarouting.com.br; the wiki read claim by claim against the product at 0.138, six pages added, renamed Prisma Routing with its logo | #479 #480 |
| 0.138.8 - 0.138.10 | the translation screen answers a reserve port; a policy term naming a missing list is refused instead of matching every route; `commit`'s words each acknowledge only themselves, and `confirmed` and `comment` go together | #483 #484 #481 |
| 0.138.11 | on a virtio guest each packet is translated on the worker that received it | #487 |
| 0.138.12 | the VTY names the command that gives a dataplane card back | #482 |
| 0.138.13 | the boot after a tuning change forwards on the workers already isolated | #486 |
| 0.139.0 | the installer names cards as the installed box will | #476 |
| 0.140.0 | a hypervisor's Shutdown stops the box cleanly | #478 |

0.139.0 and 0.140.0 went through the full gate; the rest are patches.

## The bench (#475)

Three VMs on the maintainer's Proxmox (`fix-dl360-g8`), VMIDs 200240-200242,
on two bridges of their own (vmbr9, vmbr10) with no uplink. **Nothing else on
that host is ours to touch.** 200240 runs the subscribers, 200241 is the box
(installed from the ISO, upgraded with `system upgrade`, at 0.139.0), 200242
is the internet (iperf3, IPFIX and syslog collectors). The credentials are in
the maintainer's hands and in files outside this repository, never here.

What it measured, all on the issue: BPA holds under real TCP; the ceiling is
about 6.7 Gbit/s at 1500 bytes and **it is the host**, not the box - the
workers find a packet in 7% of their polls and the guests show about 20%
steal. To measure further the guests need pinned CPUs or a passed-through NIC,
and that is the maintainer's host to decide about. Found on the way and
shipped: #486, #487.

## Decided on 2026-09-24 and after

* **#449, the kernel**: the maintainer reversed the earlier recommendation,
  with the cost stated and accepted. The medium ships Debian's source rebuilt
  as `-prisma`, and a Debian kernel security release reaches a box only when
  `appliance/kernel.env` moves (see below).
* **#448, the VTY in a container**: done. The login's VTY runs in
  `prisma-routing_mgmt_1` as the login's own uid; `/etc/prisma-routing/vty-on-host`
  puts a box back on the host's VTY.
* **#469, `--More--`**: every interactive `show` pages, by the maintainer's
  request. `| no-more` turns it off.
* **#396** and **#399** are answered for now: the dataplane, the control plane
  and the management plane are containers; whether the agent and telemetry
  get their own, and active/active, are the maintainer's.

## Waiting on a decision

* **#485**: stateful NAT64, asked for by a contributor as CGNAT with an IPv6
  inside - the same pool shared by port and the same court-order obligation.
  It takes NAT64 back out of #31's "not planned", so the scope is the
  maintainer's call; nothing is started.
* **#210**: the block rebalancer is delivered and off (`bpa rebalance
  enable`). Every setting that serves the refused subscriber also lends a
  block the lender's next subscriber wants; which one an ISP wants depends on
  real subscribers retrying, which a box with traffic measures in an
  afternoon.

## Blocked

* **#28, #29, #467** - throughput, symmetric RSS and the core-layout benchmark
  need a NIC DPDK drives natively; a virtio VM measures the hypervisor.
* **#163** - its first part is superseded by #449; what remains, the boot
  tuning, needs the same hardware.

## Deferred by their own issues

**#32** PCP, **#80** pool selection by ACL (a sub-issue of **#26**, whose VRF
half is delivered), and **#170** BNG, which says why it is a second product
rather than a feature.

## What is worth knowing before touching anything

### Releases

* **A minor's gate does not run `make test`.** Only a patch's does, and
  0.134.0 shipped with `test_install_says` red because the run that would have
  shown it was stopped on the belief that the gate would. Run `make test` to
  the end before tagging a minor, every time.
* **A suite that dies is `NO VERDICT`, not `FAIL`.** A filter for `FAIL` alone
  missed `test_prismash_igp` dying on a stub. Read the summary line
  (`N suites: N passed ...`), or all three words.
* **A patch release installs nothing, so a change a console session sees
  escapes it.** 0.136.2's pager stopped two labs at `--More--`, found only by
  the next minor's gate. The labs now type `| no-more`; a script driving a
  console session has to as well.
* **A branch cut before a release files its CHANGELOG entry inside that
  release when it is merged**, and **a union merge of `docs/roadmap.md` keeps
  both versions of a row.** Rebase onto `master` before merging, every time,
  and check where the entry landed.
* **An issue opened while a release is in its gate fails that gate** unless
  `docs/roadmap.md` on the release's branch names it. 0.136.0's first gate was
  refused for #469 and #470, opened an hour earlier.
* **`appliance/install.sh`, `appliance/build-iso.sh`, `build/Dockerfile.dev`
  and `CMakeLists.txt` are medium-only to the gate**, so a change to any of
  them is a minor with the full gate. The full gate needs about 32 GB of RAM.
* **Every release goes to the public directory as well** -
  `tools/publish-release.sh`, with the target in `~/.releases-target`.
* **A release title passed through `sh -c` loses its apostrophe** to the
  quoting, and the title is what the CHANGELOG heading and the published
  release are checked against. 0.140.0's first cut was stopped for exactly
  that; write the call into a script file and run the file.

### The kernel

* **`appliance/kernel.env` has to follow Debian's security releases.** The
  gate asks `tools/kernel-current.sh` and refuses a medium whose kernel Debian
  has replaced. Moving it is: the new source version and its ABI name in
  `kernel.env`, then `make kernel` (about 900 s on 16 CPUs, cached by version
  under `/var/cache/prisma/kernel`), then a minor. `tools/build-kernel.sh`
  refuses a build missing any NIC, storage, virtio, vfio, uio, RDMA or
  hypervisor module Debian ships, and one carrying key material.
* **The upgrade artifact carries no kernel.** A box gets a new one from the
  medium it is installed from.

### The VTY in the management plane

* **A new read of the host in `prismash.py` needs its line in
  `compose.yaml`**: an `os.environ.get("PRISMA_*", "<host path>")` seam, and
  the mgmt service's environment pointing it under `/host`.
  `test/test_mgmt_reads_host.py` refuses one without it. Read at its own path
  in the container, it is the container's - 0.137.0's first gate had the VTY
  tell the live medium's operator to reboot.
* **A new command the VTY runs on the host needs a door.**
  `test/test_doors.py` reads every `self._run` in `system.py` and every
  `self.sys._run` and `host_run` in `prismash.py` off the source. The first
  cut read `system.py` only and missed `system reboot`: a box with the daemon
  would have refused root its own reboot.
* **What acts on the host with the terminal goes through the broker**
  (`HOSTRUN`: `install`, `system upgrade`, `system dataplane restart`); what
  needs the host's network goes through the probe door, which streams.
* **Never mount `/` or `/run` into the container**: `/run/docker.sock` is a
  socket a read-only bind does not stop anybody connecting to.

### The wiki

* **A page cites no issue number** (`test_wiki_rules`): the reasoning goes on
  the page in its own words.
* **Outline rewrites bold around a code span** into asterisks inside the
  code. Leave code spans out of bold.
* **`WIKI_PULL_DISCARD=1` overwrites every page not yet published.** Publish
  first, then pull, then commit the pull.

### The machine

* **Do not edit a shell script that is running.** `sh` reads it as it goes; a
  build lost its tail to exactly that, and two kernel builds were lost to a
  quote nested three deep. A long container job is a file of its own.
* **`pkill -f` with a pattern your own command line contains kills your own
  shell.** Find the pid first.
* **A mutation that does not compile runs a stale binary green.** Watch the
  build say it built before believing a green run of a C mutation.
* **The Discord announcement needs `~/.discord-webhook`**; it was absent this
  session too, and the hook said so on every commit without failing it.
* **A lab screen needs two lab-only traps disarmed** before traffic goes
  through: the container's own veth answers ARP for the gateway, and docker's
  default route outranks a static default. A lab of the CGNAT's reach to the
  box itself also needs the pool blackholed, as every box has it, or a
  translated reply finds its way back through the upstream and hides the
  defect (#470).
* **The labs are where the interesting failures are found**, and a lab that
  asserts what the box does wrong is worse than none.
* **A service that reads a device at boot has to wait for it.** The power
  button's first cut started 0.4 s before the kernel registered the button,
  read nothing, and exited as a machine with no button; the bench did not
  show it because the service had been started there by hand (#478). A fix
  proven by starting something by hand is proven for the hand.
* **On a KVM guest the host's tap steers a flow to the queue the guest last
  transmitted it on.** That, not RSS, decides which worker receives on a
  virtio card, which is why #487 placed RX queues by TX queue. On a physical
  card RSS decides, and #29 is still the work there.
