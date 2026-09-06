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

Nothing merged since v0.6.0.

## 0.6.0 — the box you can install, and the questions you can ask it

Two of the four changes here are things the previous release got wrong, and one
of them produced a wrong answer to a court order with full confidence.

### Fixed

* **Two pools no longer share subscriber state**
  ([#57](https://github.com/andrediashexa/hexarouting/issues/57)). The
  per-worker subscriber table was keyed on the subscriber's index **within its
  own pool**, which is unique per pool and not globally, so subscriber 5 of each
  pool shared one set of port bitmaps and one list of blocks. Measured: a
  subscriber of a pool whose public range is `203.0.113.16/30` left on another
  pool's `200.150.0.17`. The wrong address is the visible half; the serious half
  is that the `natEvent 16` named the wrong pool's block, and that is the record
  handed to a court. Two customers could also be handed the same block. Unseen
  because every lab configured exactly one pool. **If you run more than one
  pool, upgrade.**
* **The ISO carried a known root password onto the disk**
  ([#52](https://github.com/andrediashexa/hexarouting/issues/52)). The image
  shipped `root:hexarouting` with `PermitRootLogin yes` and `openssh-server`
  enabled, and the installer unpacked `/etc/shadow` from the live filesystem,
  asking for a password only at the very end — after GRUB. An install aborted at
  that prompt, failing late, or run unattended left a bootable system carrying
  it.
* **hexash silently dropped pool settings it did not know**
  ([#47](https://github.com/andrediashexa/hexarouting/issues/47)). Because the
  candidate configuration is built *from* the running one, a setting the model
  could not hold was lost on the way in and **removed on the way out**: setting
  something with `vppctl` and then committing any unrelated change through the
  VTY silently reverted it. It applied to the timeouts, `max-blocks`, the
  filtering mode and the ALG switches — every per-pool setting shipped so far.
* **`show bpa config` printed every ALG override twice.** Two branches added the
  block to the same emitter and the v0.5.0 merge kept both, so every saved
  configuration on a box with an ALG override carried a duplicated line.
* **`docs/roadmap.md` described five shipped features as missing**, and still
  proposed the IPv6 transition work the maintainer had taken out of scope.

### Added

* **The ISO is an installation medium.** No credentials at all — the root
  account is locked and `ssh.service` masked — and `install` in the VTY asks for
  the disk, hostname, timezone and root password **before it writes anything**,
  then reboots. `install` exists only when the box is running from the medium.
  An install that dies halfway now leaves a disk nobody can log into rather than
  one anyone can.
* **Four show commands for the question people actually arrive with**
  ([#40](https://github.com/andrediashexa/hexarouting/issues/40)):
  `show cgnat subscriber <ip>`, `show cgnat translation <ip> <port>`,
  `show cgnat top [n]` and `show cgnat drops`. `translation` states whether its
  answer comes from the formula — which holds for any past instant — or from a
  dynamic allocation, which holds for **now only**. None of them walks the
  session table.
* **Every per-pool setting reachable from the VTY**, with the binary API grown
  to carry them (`bpa.api` **0.2.0**: `filter`, `max_blocks`, `alg`, `alg_set`
  and a `bpa_timeouts` typedef). Unknown configuration lines are now named with
  their line number; `commit` and `--load` refuse rather than trimming.
* **An ASCII banner on an interactive login**
  ([#41](https://github.com/andrediashexa/hexarouting/issues/41)), 59 columns of
  plain ASCII, absent whenever output is not a terminal.
* **Per-subscriber port-refusal counting**, on the failure path only. "Is it the
  box or is it them" is not answerable from a pool-wide counter.

### Not delivered, and why

* **`show cgnat top` is not the histogram
  ([#27](https://github.com/andrediashexa/hexarouting/issues/27)) asks for.** It
  ranks subscribers by how full their blocks are, which answers the same
  question for a human reading a screen; a distribution and a threshold alarm
  are what an NMS needs, and they remain open.
* **The two TCP sequence-adjustment mechanisms are still two**
  ([#48](https://github.com/andrediashexa/hexarouting/issues/48)).

### Known gaps

Unchanged and still ahead of the additions: throughput has never been measured
([#28](https://github.com/andrediashexa/hexarouting/issues/28)), there is no HA
([#6](https://github.com/andrediashexa/hexarouting/issues/6)), no authorization
in the VTY ([#35](https://github.com/andrediashexa/hexarouting/issues/35)), and
the audit trail records what changed but not who
([#34](https://github.com/andrediashexa/hexarouting/issues/34)).

## 0.5.0 — the protocols that break quietly

Three application-level gateways, and a claim about this NAT's shape that
stopped being a claim.

Every ALG here exists for the same reason: a protocol puts an address or an
identifier **inside its payload** and then expects the peer to use it. The
failures that follow are the expensive kind — the connection establishes, the
exchange succeeds, and the thing the exchange was about silently does not
happen. Nothing in the CGNAT looks wrong while it happens.

### Added

* **FTP ALG** ([#42](https://github.com/andrediashexa/hexarouting/issues/42)). `PORT`, `EPRT` and the `227`/`229` replies are
  rewritten with the subscriber's public address and a port from its **own**
  block, so the mapping stays derivable. The announced data connection is
  installed as a session before it arrives, half-open so an announcement the
  server never answers costs what a scan probe costs. Sequence numbers are
  shifted in one direction and acknowledgements back in the other — without
  that the control connection stalls a few commands *after* the rewrite, which
  looks like neither FTP nor NAT.
* **RTSP ALG** ([#43](https://github.com/andrediashexa/hexarouting/issues/43)). The `Transport:` header of a `SETUP` names the
  ports the client wants its media on. Behind the CGNAT those are private, so
  the server sends RTP to a public port belonging to nobody: the IP camera that
  works on the LAN and shows a black frame from outside. A consecutive,
  RTP-even port pair is reserved from the subscriber's own block, on the same
  public address as the control connection.
* **PPTP and GRE ALG** ([#44](https://github.com/andrediashexa/hexarouting/issues/44)). GRE has no ports; what tells one tunnel
  from another is a 16-bit Call ID each end picks for itself, and Windows and
  most routers pick from the bottom of the range. Two subscribers behind one
  public address both choosing `1` is the normal case, not a corner case.
  **Without this, PPTP did not work at all** — not merely on a collision: the
  Call ID falls outside every port block, so the inbound arithmetic pre-check
  rejected it before any lookup. GRE is now a protocol of its own and the Call
  ID is allocated from the subscriber's block and rewritten like a source port,
  which is what lets the pre-check, the worker handoff and the RFC 8158 log
  handle a PPTP call without knowing PPTP exists.
* **`show bpa alg`**, and per-pool ALG switches changeable on a box carrying
  traffic: `bpa alg <pool> <ftp|rtsp|pptp> <on|off>`. An operator who suspects
  an ALG of corrupting a stream needs to switch it off without disconnecting
  everybody, or the switch is unusable at the moment it is needed.
* **A GRE idle timeout and an ALG-expectation timeout**, global and per pool.
  The second is short by design: a media mapping nothing ever uses would
  otherwise sit on the UDP timeout, and a camera reconnecting in a loop turns
  that into exhaustion of its own subscriber's block.

### Changed

* **One ALG registry.** The three ALGs were built in parallel and arrived with
  three different switch designs. `foreach_bpa_alg` now names each one once and
  the CLI, the running configuration, `show bpa pool` and `show bpa alg` are
  generated from it. The pool carries two masks — what is enabled, and what the
  operator has actually stated an opinion about — so a pool built from a zeroed
  structure gets the documented defaults without every creation path restating
  them, and an ALG that ships **off** stays expressible.
* **All three ALGs default on**, against the roadmap's blanket "default off".
  The departure is argued per ALG in `docs/roadmap.md` rather than made
  silently: each of these three has a parse that can be made strict, and each
  is a protocol that does not work at all without its ALG.
* **`tools/lab.sh` passes its arguments to the lab.** That is what lets a lab be
  run with the feature it tests switched off, which is the only way to know the
  lab can fail at all.

### Fixed

* **Full-cone NAT is now asserted rather than reasoned about**
  ([#50](https://github.com/andrediashexa/hexarouting/issues/50)). The behaviour was already there and already the default;
  the lab sent an unsolicited packet from an uninvited remote to an open
  mapping, stored the result in a variable and never mentioned it in the
  verdict. A change that turned the default into a restricted cone would have
  broken every peer-to-peer application behind this box and passed the whole
  suite.
* **`lab/nat-behaviour.sh` was asserting on packets that reached nobody.** `pg1`
  had no address, so IPv4 was not enabled on the interface inbound packets
  arrive on and a correctly translated packet died in `ip4-not-enabled` *after*
  the NAT had done its work — counter said "translated", subscriber received
  nothing.
* The last Portuguese comments and output strings in the plugin and in
  `tools/lab.sh`.

### Not delivered, and why

* **SIP, TFTP and IPsec ESP passthrough.** Still open under
  [#14](https://github.com/andrediashexa/hexarouting/issues/14). SIP is the most expensive support call an ISP receives and
  also the ALG most likely to misparse, which is why it is not being rushed in
  beside three others.
* **The two sequence-adjustment mechanisms were not merged into one**
  ([#48](https://github.com/andrediashexa/hexarouting/issues/48)). FTP and RTSP each brought one and they are the same idea
  twice. Folding two well-tested mechanisms together during a merge, with no
  test that would catch a mistake, is how a merge breaks two working features
  at once. A connection is either FTP or RTSP, so the duplication costs bytes
  rather than correctness.
* **Incoming PPTP calls, split control messages and fragmented GRE.** Each is
  named in `docs/architecture.md` with the reason. A subscriber behind a CGNAT
  cannot receive an unsolicited call in the first place.

### Known gaps

One is new and worse than it looks: **`hexash` silently drops any `pool`
sub-line its model does not know** ([#47](https://github.com/andrediashexa/hexarouting/issues/47)). That is not a gap in one
feature — it means `filter` ([#15](https://github.com/andrediashexa/hexarouting/issues/15)), `max-blocks` ([#13](https://github.com/andrediashexa/hexarouting/issues/13)) and the
per-pool timeouts ([#12](https://github.com/andrediashexa/hexarouting/issues/12)) do not exist through the VTY, which is the
interface an operator actually uses. Their labs pass because they drive
`vppctl` directly.

Otherwise unchanged: throughput is not measured ([#28](https://github.com/andrediashexa/hexarouting/issues/28)), there is no HA
([#6](https://github.com/andrediashexa/hexarouting/issues/6)), no authorization in the VTY ([#35](https://github.com/andrediashexa/hexarouting/issues/35)), and the audit trail
records what changed but not who ([#34](https://github.com/andrediashexa/hexarouting/issues/34)).

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
