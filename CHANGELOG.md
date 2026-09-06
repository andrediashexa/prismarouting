# Changelog

## TL;DR

Every merge into `master` is a release: a version bump, an annotated tag and
an entry here. The project stays on `0.x.x` until the maintainer declares it
ready for production, so breaking changes are permitted and are called out
under their own heading. Newest release first.

The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
are to be interpreted as described in
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## 5W2H

| | |
|---|---|
| **What** | The record of what changed in each released version of HexaRouting. |
| **Why** | An operator reporting a fault can say which version they run; without this file that number means nothing to anyone. |
| **Who** | Written by whoever prepares the merge, read by whoever runs the box. |
| **Where** | This file, alongside the annotated tag carrying the same note. |
| **When** | At merge time, never afterwards from memory. |
| **How** | One section per version: behaviour changes first, then fixes, then internal work, each naming the issues it closes. |
| **How much** | A few lines per release. An entry that needs a page is a merge that should have been two. |

## Unreleased

Nothing merged since v0.4.0.

## 0.4.0 — what the subscriber notices

Four changes an operator meets in the first month of running a CGNAT, and one
claim that stopped being a claim.

### Added

* **Hairpinning** ([#16](https://github.com/andrediashexa/hexarouting/issues/16)). Two subscribers behind the same CGNAT can now
  reach each other through a public address (RFC 4787 REQ-9). Without it the
  packet was translated correctly and then routed to an address this box owns,
  with nothing to send it back in — a camera that answers from the street and
  not from next door, a service that works for everyone except the neighbours.
  B sees A's **public** address, as REQ-9 requires: presenting the private one
  would leak the inside topology and break B's reply path.
* **Extended blocks** ([#13](https://github.com/andrediashexa/hexarouting/issues/13)). A subscriber that fills its block is given
  another, up to `max-blocks`, instead of being dropped. The cap matters as much
  as the extension — without one an infected host consumes the pool everyone
  else needs.
* **Per-pool timeouts** ([#12](https://github.com/andrediashexa/hexarouting/issues/12)). The pool is the unit an operator sells,
  so it is the unit of policy. Zero inherits the global.
* **A separate timer for half-open TCP sessions.** A host scanning the internet
  produces one SYN-without-establish per probe; held for the transitory timeout
  they exhaust the subscriber's own block, and the complaint arrives as "the
  internet stopped" from a dataplane that looks healthy.
* **Port hold-down.** A released port waits before being handed out again, so a
  delayed segment from the connection that just ended cannot land on the new
  one — a reset nobody can reproduce.
* **Address-dependent filtering, per pool** ([#15](https://github.com/andrediashexa/hexarouting/issues/15)), switchable in place
  with `bpa filter` so changing it does not disconnect anyone.

### Changed

* **The claim that this NAT is endpoint-independent is now measured.**
  `docs/roadmap.md` asserted it and offered a hash key as evidence — a correct
  reading, and still only a reading. `lab/nat-behaviour.sh` drives one internal
  socket to two remotes and checks it keeps one public port. That test passes on
  the previous release too, which is the point: it confirms behaviour rather
  than proving new work, and a change that breaks it now fails a test.

### Not delivered, and why

* **Extended blocks in deterministic mode.** That mode has no spare block by
  construction: `capacity = outside.count * blocks_per_ip` and the formula
  assigns every block to some subscriber, so a block handed out as an extension
  is one taken from somebody else, breaking their mapping with no error
  anywhere. It needs a reserve carved out of capacity, which re-maps every
  existing subscriber — [#39](https://github.com/andrediashexa/hexarouting/issues/39), and it depends on the documented
  procedure for a capacity change ([#23](https://github.com/andrediashexa/hexarouting/issues/23)).
* **Hairpinning was exercised with UDP.** TCP and ICMP take the same code path,
  but taking the same path is not the same as being tested, and that is exactly
  the assumption this project keeps getting caught by.

### Known gaps

Unchanged and still ahead of the additions: throughput is not measured
([#28](https://github.com/andrediashexa/hexarouting/issues/28)), there is no HA ([#6](https://github.com/andrediashexa/hexarouting/issues/6)), no ALG ([#14](https://github.com/andrediashexa/hexarouting/issues/14)), no
authorization in the VTY ([#35](https://github.com/andrediashexa/hexarouting/issues/35)), and the audit trail records what
changed but not who ([#34](https://github.com/andrediashexa/hexarouting/issues/34)).

## 0.3.0 — a control plane that reports failure, and an ISO that installs

Two things an operator could not do before this release: trust that `commit`
means the dataplane accepted the configuration, and install the appliance from
its own image.

### Added

* **`hexash` drives the dataplane through the binary API** ([#3](https://github.com/andrediashexa/hexarouting/issues/3),
  [#4](https://github.com/andrediashexa/hexarouting/issues/4), [#5](https://github.com/andrediashexa/hexarouting/issues/5)). `vppctl` exits 0 even when the command failed, so
  `commit` could report success while the dataplane held none of the
  configuration. A refused pool now fails the commit, names the pool, and
  leaves the candidate intact. Five outcomes are distinguished — applied,
  refused, dataplane down, plugin missing, client missing — because they need
  four different actions from the operator.
* **The ISO installs to disk** ([#33](https://github.com/andrediashexa/hexarouting/issues/33)). `hexarouting-install <disk>`,
  offered as a boot menu entry. It never touches a disk it was not given,
  refuses the medium it booted from, and confirms by having the disk path
  typed back rather than with a yes/no.
* **Logging in lands in the VTY** ([#37](https://github.com/andrediashexa/hexarouting/issues/37)), over console or SSH.
  `start shell` opens a real shell as a child and returns to the VTY on exit;
  it appears in neither completion nor `?`. **It is hidden, not restricted** —
  anyone who can log in can run it, and the authorization boundary is
  [#35](https://github.com/andrediashexa/hexarouting/issues/35), not this.
* **MTU is an explicit interface parameter** ([#2](https://github.com/andrediashexa/hexarouting/issues/2)), applied to both the
  VPP interface and its `linux_cp` TAP. `docs/routing.md` had said normatively
  since the routing work that MTU **MUST** be explicit and never inherited,
  and the VTY did not implement it.
* **`make hw-up` / `hw-vpp` / `hw-down`** ([#38](https://github.com/andrediashexa/hexarouting/issues/38)) give the hardware
  lab's long-lived container an owner.

### Fixed

* **Hugepages are bounded by physical RAM.** The tuner sized them from the
  subscriber target alone, so a 3 GB box was told to reserve 5 GB, VPP started
  against a heap that was not there, and it died with SIGSEGV in a restart
  loop. `hexarouting-tune` exists to prevent quiet half-throughput failures and
  was producing a dataplane that did not run at all.
* **The main heap is sized from the pages actually reserved**, not from a
  constant 2G. Reserved pages are ground truth; the request is an intention.
* **The tuned profile is not emitted when the tuner decided this machine
  should have no hugepage pool.** Pages can be reserved for reasons a given run
  knows nothing about, including VPP pre-allocating them during its own crash
  loop, and a tuner that argues with itself across boots is worse than one that
  is merely conservative.
* **`install.sh` installs the whole `hexash` module directory.** Naming files
  one at a time shipped a missing `config.py` in 0.2.0 and a missing
  `vppapi.py` the moment the binary API landed. The appliance test now reads
  `hexash.py`'s own imports and requires each module to be present.
* **`set interface mtu packet <n>`**, not `set interface mtu <n>`. The second
  changes the hardware maximum frame size, which most drivers refuse — while
  `vppctl` still exits 0. The result is read back rather than inferred.
* **`apply-tuning` writes its reasoning to the journal.** Diagnosing a box
  whose profile did not match its hardware meant guessing across whole build
  cycles, because the one component that knew why discarded it.

### Breaking

* A box upgraded without the current `install.sh` has no vat2 plugin, and
  **every commit touching a pool will fail** with a message naming what is
  missing. That is deliberate: the alternative is falling back to parsing CLI
  text, which is the defect this release removes.

### Tests that failed before their fix

Recorded because the value of this release is mostly in what can now fail:

* An assertion reported the boot tuning as applied on a boot whose console had
  printed only `Welcome to GRUB!`. Absence of a string is not evidence when
  there was no output to search.
* A rollback assertion passed while nothing had changed — 9000 restored to
  9000 is true for the wrong reason. It is now guarded by a check that the
  value moved at all.
* An assertion demanded the tuned profile from a box with too little RAM for
  one, failing a tuner that was behaving exactly as designed.

## 0.2.1 — the VTY starts

**Anyone running 0.2.0 should replace it.** The ISO published with 0.2.0 boots,
forwards and passes its self-test, but `hexash` — the VTY, the way an operator
configures the box — did not start at all.

### Fixed

* **`hexash` was never installed completely.** `install.sh` copied
  `src/hexash/hexash.py` to `/usr/bin/hexash` and nothing else, while
  `hexash.py` does `from config import Config, Pool`. Every installed box and
  every ISO since the configuration model was added shipped a VTY that raised
  `ModuleNotFoundError` on start.

  Both files now install into `/usr/lib/hexarouting/`, reached through a
  launcher at `/usr/bin/hexash` rather than a symlink: how Python derives
  `sys.path[0]` for a symlinked script has changed between releases, and an
  appliance should not depend on which one it got.

### Why no test caught it

Three tests stood around the failure and none went through it. The labs run
`hexash` from the source tree, where `config.py` happens to sit beside it;
`test_startup_conf.sh` ran `install.sh` but never invoked the VTY; and the ISO
boot test never logged in.

`test_startup_conf.sh` now runs the **installed** `hexash` and asserts it starts
and reaches the dataplane. That assertion was verified to catch the original
defect, by deleting `config.py` from the installed tree and watching it fail.

The gap this closes is not "a missing file". It is that the release was declared
verified at five levels, correctly for what each level measured, and none of
them measured whether the product's front door opened.

## 0.2.0 — foundations

The first release cut under the rules in
[CONTRIBUTING.md](CONTRIBUTING.md). It changes no packet-forwarding behaviour:
everything here is about making the project legible, versioned and honest
about what it is.

### Added

* **Versioning and releases** ([#10](https://github.com/andrediashexa/hexarouting/issues/10)). The project stays on `0.x.x`
  until the maintainer declares it production-ready. Every merge into `master`
  now produces a version bump, an annotated tag and an entry in this file.
  `VERSION` is the single source of truth, and every tag is published as a
  GitHub Release carrying the same note and the ISO — a tag is a pointer, and
  a release is where an operator who does not clone the repository reads what
  changed.
* **The version is visible on the box.** The boot self-test prints it as its
  first line, `hexash` reports it under `show version` next to the VPP and FRR
  versions, and `lab/boot-iso.sh` fails an ISO that boots without a stamp. A
  version that lives only in git identifies the release to whoever has the
  repository open, which is not the person reporting a fault.
* **`tools/version-stamp.sh`**, which degrades visibly: `dirty`, `unverified`,
  `not a git tree`, `no git at build time`. An artifact built from uncommitted
  changes says so instead of claiming to be a release.
* **Project rules** ([CONTRIBUTING.md](CONTRIBUTING.md)): documentation in
  English opening with a TL;DR, 5W2H and RFC 2119 keywords, Mermaid for
  diagrams, one issue per feature, one branch per issue, sub-issue branches
  cut from the parent. The rules bind GitHub text as well as the tree.
* **GitHub issue templates**, including the field this project keeps needing:
  which test should have failed and did not.
* **[`docs/roadmap.md`]** ([#11](https://github.com/andrediashexa/hexarouting/issues/11)): what an ISP-grade CGNAT still needs,
  what is already satisfied by this design, and a delivery plan mapping all 32
  issues onto nine release milestones.

### Changed

* **Every source comment, docstring and assertion message is now English**
  ([#1](https://github.com/andrediashexa/hexarouting/issues/1)) — 74 files across `src/`, `ref/`, `test/`, `lab/`, `appliance/`
  and `tools/`.

### Fixed

Three assertions in the labs could not fail, all the same shape: the labs were
written in Portuguese, the product's strings moved to English in earlier work,
and the needles stayed behind. A grep that matches nothing looks exactly like
a grep that found nothing wrong.

* `lab/dynamic.sh` searched for `"sem bloco livre"`; the plugin emits
  `"no free block in dynamic pool"`. The refusal counter showed as empty
  output.
* `lab/dynamic-workers.sh` searched for `"sem sessao|bloco nao alocado"` and
  printed `"nenhum"` on every run regardless of what happened.
* `lab/ipfix-tcp.sh` asserted `expect_not_in "270 pendentes"` while the plugin
  prints `"%Lu pending"` — a formal assertion that passed by construction.

Also: `lab/ipfix-tcp.sh` grepped Portuguese out of `show bpa logging`, so its
diagnostic block printed nothing in exactly the phase that kills the
collector. And `install.sh` created `/etc/vpp/startup.conf.d`, a directory VPP
never reads, inviting an operator to drop files into it that do nothing.

### Known gaps

* MTU is still not an explicit interface parameter in the VTY ([#2](https://github.com/andrediashexa/hexarouting/issues/2)),
  although `docs/routing.md` states normatively that it MUST be. Moved to
  v0.3.0.
* `hexash` still drives the dataplane through `vppctl` text, which returns 0
  on failure ([#3](https://github.com/andrediashexa/hexarouting/issues/3)). This is the next release.
* No HA ([#6](https://github.com/andrediashexa/hexarouting/issues/6)), and no measured throughput ([#28](https://github.com/andrediashexa/hexarouting/issues/28)).

## 0.1.0 — pre-rules baseline

The state of `master` before versioning existed, recorded here so that later
entries have something to be relative to. Not tagged retroactively: a tag
created today for work merged earlier would claim a release that never
happened.

What it contains:

* **CGNAT with allocated port blocks (BPA)** on VPP 26.06 — deterministic and
  dynamic modes, selectable per pool, over non-contiguous address sets.
* **Routing** via FRR 10.7.1 (OSPF, IS-IS, BGP) through `linux_cp`/`linux_nl`.
* **`hexash`**, a VTY with completion, contextual help, and a Juniper-style
  configuration model: candidate, `commit`, `commit check`,
  `commit confirmed`, `rollback`.
* **IPFIX export** of block allocations only (RFC 8158), over UDP and TCP.
* **Appliance**: hardware-derived boot tuning, systemd units, installer, and
  a bootable ISO with a boot self-test.
* **Tests**: core suites, 15 self-asserting dataplane labs, two appliance
  profiles, and a booted-ISO check.
