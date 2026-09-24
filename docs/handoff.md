# Where this stands, for whoever picks it up next

Written 2026-09-24, at 0.125.1, so the next session starts with the state
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

## Released since the last handoff (0.110.0)

The CHANGELOG has each one. By theme:

| | | |
|---|---|---|
| 0.110.1 - 0.112.7 | readback validators, the release clock, the X.Y.Z reader, a block that moves between workers | #418 #419 #408 #409 #210 #423 - #425 |
| 0.113.0 - 0.118.1 | the dataplane and control plane as containers, carried in the medium, and the default that follows the images | #396 #426 #427 |
| 0.117.3 - 0.123.4 | every C function in the plugin has a unit or says why not, and every CLI message is held by a lab | #390 |
| 0.119.0 - 0.122.0 | the box a customer has - a real card, its registers, and the release that installs and boots it | #428 #429 |
| 0.122.1 - 0.122.5 | the kernel's remaining tuning, and three screens that said less than they knew | #163 #430 |
| 0.122.6 - 0.124.0 | asked for by the maintainer or found on a box, below | |

The last run, in the order they were released:

| | | |
|---|---|---|
| 0.122.6 | a statement FRR does not accept is refused where it is typed | #431 |
| 0.122.7 | the login banner spells the product name | #433 |
| 0.122.8 | a public directory to download each release from | #439 |
| 0.122.9 | Ctrl-C at the prompt no longer ends the ssh session | #441 |
| 0.122.10 | static routes as `<prefix> next-hop <address>`, with description, tag and distance | #436 |
| 0.123.0 | DNS resolvers from the VTY, which a DHCP lease no longer undoes | #437 |
| 0.123.1 | `lab/vty-ssh.sh` asserts today's box | #434 |
| 0.123.2 | `show bpa translation` names the right subscriber with workers | #432 |
| 0.123.3 | a loss at dataplane shutdown is said | #435 |
| 0.123.4 | every message the plugin's CLI prints is held by a lab | #390 |
| 0.123.5 | **security**: a shell for administrators only | #442 |
| 0.123.6 | `system upgrade` from the VTY | #440 |
| 0.124.0 | the installed system calls itself Prisma Routing | #438 |
| 0.124.1 | the wiki walks an operator through configuring the CGNAT | #443 |
| 0.124.2 | the gate boots and installs on both guests at once: 29 minutes off a minor | #409 |
| 0.124.3 | a dynamic pool nothing exports is warned | #444 |
| 0.124.4 | an applied route no longer says "No changes found" | #445 |
| 0.125.0 | the upgrade artifact carries the container images | #427 |

Of these, 0.123.0, 0.124.0 and 0.125.0 went through the full gate; the rest
are patches. 0.125.0 was the first through the parallel one.

## Decided on 2026-09-24

* **#438, `uname`**: the maintainer asked for a recommendation, and it was to
  keep Debian's kernel. Only `uname -v` still says Debian, and a kernel of our
  own ends the security updates. If that is overruled, it is a new issue.
* **#427**: the artifact carries the images (0.125.0).
* **#409**: re-aimed at the boots and closed (0.124.2). The versioned rootfs
  is not built, because what it could save is 7.6% of a release.
* **#337**: not to be done. Only the maintainer tests boxes for now, so there
  is nobody to disclose to.
* **#396** and **#399** are answered for now: containers are delivered to
  phase 2, and active/active is deferred by decision (2026-09-19).

## Blocked

* **#28, #29** - throughput and symmetric RSS on a NIC DPDK drives natively.
* **#172**, its last part - giving VPP's main thread a core of its own needs a
  barrier measurement on real hardware; a virtio VM measures the hypervisor.
* **#163** - what remains needs the same hardware.
* **#36** - Debian 13: fd.io still publishes no trixie suite (re-tested
  2026-09-24: 404).

## Deferred by their own issues

**#32** PCP, **#80** pool selection by ACL (a sub-issue of **#26**, which adds
VRF), and **#170**
BNG, which says why it is a second product rather than a feature.

## What is worth knowing before touching anything

* **A branch cut before a release files its CHANGELOG entry inside that
  release when it is merged**, cleanly and with no conflict: git anchors it on
  the `### Fixed` it still sees. `test/test_changelog_landing.py` and the gate
  refuse it, and the gate refused 0.123.2's first cut for exactly that. Rebase
  a branch onto `master` before merging it, and check where the entry landed.
* **A union merge of `docs/roadmap.md` keeps both versions of a row**: the
  open one and the delivered one. `tools/roadmap-state.sh` reads the issue a
  row names, not the word next to it, so it passes. `test/test_doc_copies.py`
  catches a duplicated sentence, and it refused 0.124.4's first cut for
  exactly that. Rebase a branch onto `master` before merging it, every time.
* **`appliance/install.sh` is medium-only to the gate**, so any change to it
  is a minor release with the full gate, whatever the change is. 0.122.11 was
  refused as a patch for that and re-cut as 0.123.0. The full gate needs about
  32 GB of RAM.
* **Every release goes to the public directory as well** -
  `tools/publish-release.sh`, with the target in `~/.releases-target`. The
  GitHub release page is private, and the wiki and `system upgrade` both send
  operators to the directory.
* **A mutation that does not compile runs a stale binary green.** Watch the
  build say it built before believing a green run of a C mutation.
* **The Discord announcement needs `~/.discord-webhook`** on the machine that
  commits. It was not on the machine this session ran on, so none of its
  commits was announced - the hook says so on every commit, and does not fail
  it.
* **A lab screen needs two lab-only traps disarmed** before traffic goes
  through: the container's own veth answers ARP for the gateway (VPP drops
  the frames as `l3 mac mismatch`), and docker's default route (distance 0)
  outranks a static default, so the dataplane's default is `drop`. #443's
  capture met both.
* **The labs are where the interesting failures are found**, and a lab that
  asserts what the box does wrong is worse than none. Both were true again
  this session: `lab/dynamic.sh` was blamed for a red that was the screen's
  defect (#432), and `lab/vty-ssh.sh` had asserted a refusal sshd no longer
  gets to make for thirteen minors (#434).
