# Where this stands, for whoever picks it up next

Written 2026-09-27, at 0.138.0, so the next session starts with the state
rather than reconstructing it. **This file is a handoff, not a plan** —
`docs/roadmap.md` is the plan, and where the two disagree the roadmap is
right. `CLAUDE.md` is how this repository is worked on; read that first.

## The short version

**Nothing is on a branch waiting.** Every issue that was work somebody could
start has been released and closed. What is open is one of three things, and
none of them is waiting for somebody to start it:

* **a decision the maintainer has not taken**, with the evidence already on
  the issue;
* **blocked**, on hardware this project does not have or on an upstream;
* **deferred by its own issue** until there is demand for it.

The next piece of work will therefore come from the maintainer: a decision on
one of the issues below, or, as it usually has, something seen on a box.

## Released since the last handoff (0.125.1)

The CHANGELOG has each one. By theme:

| | | |
|---|---|---|
| 0.125.2 - 0.130.0 | the CGNAT under `nat`, a backplane of its own (128.0.0.0/8 in VRF `prisma-bp`), the login broker, cards added to a running box, the CGNAT's memory, the dataplane under `system` | #446 #447 #448 #453 #454 |
| 0.131.0 - 0.131.8 | the maintainer's box, one request at a time: the Linux end named for its interface, commit from inside a pool, the CGNAT side on the interface, BPA as the dynamic allocation, the RAM floor, Junos-style routing policy, a first allocation larger than the step, cores by role | #461 #462 #463 #464 #452 #458 #465 #466 |
| 0.132.0 - 0.133.4 | a smaller initrd, policy into OSPF and IS-IS, the control plane fenced off the dataplane's CPUs, `show`/`test policy`, memory by role, RX and CGNAT workers, LACP bundles | #450 #458 #466 #455 |
| 0.134.0 | the NIC drivers back in the initramfs: 0.132.0 booted with its cards missing | #468 |
| 0.135.0 | the host's doors: what the VTY does to the host, asked of a root daemon | #448 |
| 0.136.0 | the kernel is Prisma Routing's: Debian's 6.1.187-1, renamed and trimmed | #449 |
| 0.136.1 | a subscriber reaches the box itself through the CGNAT | #470 |
| 0.136.2 | a `show` longer than the screen stops at `--More--` | #469 |
| 0.137.0 - 0.138.0 | the management plane's container, and the VTY in it by default | #448 |

0.132.0, 0.133.0, 0.134.0, 0.135.0, 0.136.0, 0.137.0 and 0.138.0 went through
the full gate; the rest are patches.

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
* **#36** - Debian 13: fd.io still publishes no trixie suite.

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
