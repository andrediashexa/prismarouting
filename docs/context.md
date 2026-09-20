# Prisma Routing - Context model

## TL;DR

Read this before reading anything else in the repository. It does not describe
the design — [architecture.md](architecture.md) does, and a second copy would
diverge from the first with no way to tell which one is stale. It states the
handful of facts that make the rest legible, and says which file is
**authoritative** for each one.

Eleven invariants, a map of what question each part answers, and a reading order
per task. Roughly fifteen minutes.

> The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
> are to be interpreted as described in
> [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## 5W2H

| | |
|---|---|
| **What** | The orientation needed to read a dataplane and a VTY of more than ten thousand lines each, and a lab per subject, without reconstructing the shape first. |
| **Why** | The design is recorded across the files section 2 maps, each written for a reader who already knows why that file exists. None of them is written first. |
| **Who** | Anyone starting an analysis — a new maintainer, a reviewer, or a model. |
| **Where** | Here; every claim points at the file that owns it. |
| **When** | Before the first question. Several pieces of work in this repository started from an assumption one of the invariants below would have corrected. |
| **How** | Invariants, then a map, then a reading order. |
| **How much** | One page. Anything longer is a second architecture document. |

## 1. The eleven things that are not obvious from any one file

### 1.1 The mapping is arithmetic, not a table

A subscriber's public address and port block are **computed** from the pool
definition. There is no allocation record to consult, which is the entire point
— and it means changing the definition changes the function. Adding one public
prefix moves **every** subscriber at that instant and ends every session on the
pool.

Nothing is corrupted and past answers stay resolvable, but every customer is
disconnected at once and the box reports nothing unusual, because nothing
unusual happened. `commit` refuses such a change until it is asked for by name.

*Authoritative:* [pool-resize.md](pool-resize.md), `bpa_pool.h`.

### 1.2 In deterministic mode, the configuration **is** the log

There are no allocation events, so there is nothing to export. The proof
offered to an authority is *"the algorithm, plus the configuration in force at
that instant"* — which holds only while it is known **which** configuration was
in force.

**One exception, and it proves the rule.** A pool with a `reserve` holds blocks
out of the formula and gives them to subscribers that fill their own
([#39](https://github.com/andrediashexa/hexarouting/issues/39)). Those blocks
are exactly the ones the algorithm does **not** produce, so the configuration
cannot answer for them and each one is exported as an RFC 8158 `natEvent 16`
with its release as a `natEvent 17`, like a dynamic block. The reserve is also
part of the definition the trail records, because it is part of the arithmetic:
without it a reader recomputes the wrong mapping, confidently. That is what `bpa_audit.c` exists for, and why it is append-only,
`fsync`ed, carries the whole definition rather than a diff, and now carries the
operator who made the change.

A trail entry that cannot be attributed **MUST** say so; omitting the field
would be indistinguishable from an entry where nobody was accountable.

*Authoritative:* [logging.md](logging.md#configuration-trail), `bpa_audit.c`.

### 1.3 There are two specifications of the mapping, and they MUST agree

`src/plugins/bpa/bpa_pool.h` is the implementation. `ref/bpa_model.py` is the
**executable specification**: it computes the same mapping from the
specification rather than from the implementation, and `make test` compares
every generated vector against it and prints the counts. Any divergence
**MUST** break the build.

This is why a test that asks the dataplane to confirm its own arithmetic is
worth nothing here — the oracle exists precisely so it does not have to.

*Authoritative:* [TESTING.md](../TESTING.md), `ref/validate.py`.

### 1.4 A counter is not evidence

The recurring defect class in this project is an artifact that reports success
without verifying effect: `vppctl` exits 0 on failure; an ALG increments its
counter whether or not the rewrite was correct; a bypass implemented after the
allocation passes "left untranslated" and fails "allocated nothing".

So labs assert on the **captured packet** or the **trail**, not on counters —
and every feature lab **MUST** be runnable with the feature switched off and
**MUST** then fail. An assertion that has never failed has not been shown to
test anything.

*Authoritative:* [CONTRIBUTING.md](../CONTRIBUTING.md),
[TESTING.md](../TESTING.md).

### 1.5 The box stays reachable through the card it claims, and never reserves the last one

DPDK binds a NIC away from Linux. Hand over the port you administer the box
through and it is unreachable, and unreachable is unfixable remotely. For two
years that argument made the VTY, the tuner and `bind-dataplane-nic` all
**refuse** the card carrying the default route wherever there was another card
to name instead ([#123](https://github.com/andrediashexa/hexarouting/issues/123)).
The refusals went in
[#385](https://github.com/andrediashexa/hexarouting/issues/385), on the
maintainer's decision: a box is configured at its console when it is new, so
a refusal guarding the card it is reached over guards a step nobody takes over
the network. A card named by hand is claimed whichever route it carries. On
the lab machines the management card is `ens18`.

**On a box with one card the refusal was a dead end anyway**, and that is
[#303](https://github.com/andrediashexa/hexarouting/issues/303): it sent the
operator to name a card the box has not got, so the box could not forward and
could not be told to. Since
[#378](https://github.com/andrediashexa/hexarouting/issues/378) the card goes
without being named: the box claims every card it has except one reserved for
Linux - the management network the installer recorded - and **never the last
one**.

The box works out how to stay reachable before it takes the card.
`prisma-routing-tune` reads that card's address, MAC and gateway off it on the
boot that claims it, in the window before anything is unbound, and the VTY
puts them back: the address onto the `linux_cp` pair the dataplane keeps for
that card, MAC and all, and the default route into Linux behind it. A card
whose way back cannot be read is **not** claimed. And if the dataplane will
not start at all, `appliance/give-the-card-back` returns the card to its
kernel driver
([#379](https://github.com/andrediashexa/hexarouting/issues/379)).

Unreachable is fixable remotely there because the box, not the operator, is
what fixes it. That was true when a thirty-minute confirmed commit undid an
unconfirmed handover, and it is true now for a better reason: nothing has to
be undone, because the way back is computed on every boot rather than
remembered from one commit.

*Authoritative:* [appliance.md](appliance.md), [routing.md](routing.md),
`lab/hw-lab.sh`, `lab/install-iso.sh` (PHASE 4 and 5: the box takes its own
card and answers from outside afterwards).

### 1.6 The VTY changes the dataplane through vat2 — one process per call

Not a persistent connection. Every change goes over the binary API; what still
goes over `vppctl` text is reading — `show interface`, `show lcp`,
`show bpa config` — and the pool validation behind `commit check`, which has no
API message of its own.

The API half decides more than it looks: it is why an identity has to travel
*inside each API message* (there is no connection to attach it to), and why the
transport — shared memory — carries no peer credentials the plugin could check
that identity against.

*Authoritative:* `src/prismash/vppapi.py`, `bpa.api`.

### 1.7 Some settings rebuild the pool and some do not, and the split is load-bearing

Changing a prefix, block size, port range, policy, mode, VRF or `reserve`
changes the mapping, so the pool is rebuilt and every session on it ends. The
reserve is on that list because it is taken out of capacity, and capacity is
the mapping. Everything else —
timeouts, ALG switches, bounds, the MSS clamp, bypass lists, forwards — is
applied **in place** on a pool that is carrying traffic.

Getting this wrong is wrong in both directions: a mapping field classified as
in-place never reaches the dataplane; an in-place field classified as mapping
disconnects a subscriber base to turn one ALG off.

*Authoritative:* `Pool.lines(in_place=)` and `Config.delta_to` in
`src/prismash/config.py`; `test/test_prismash_config.py` pins it, including an
assertion that every field a pool has is classified.

### 1.8 A release is a version bump, an annotated tag, a CHANGELOG entry and a published Release

All four, every time, or none of them. One issue per feature, one branch per
issue, sub-issues cut from the parent's branch. The project stays on `0.x.x`,
so breaking changes are permitted and are called out rather than discovered.

**This used to read "every merge into `master` is a release", and that is not
what happens** ([#324](https://github.com/andrediashexa/hexarouting/issues/324)).
`CONTRIBUTING.md`, which this section cites as authoritative, excepts a whole
class in as many words: a fix merged under the standing exception *"does not
cut a release. It rides in the next tagged one"* - because a version number
that moves for a corrected typo tells an operator nothing about whether they
should upgrade. Measured on this repository: **300 first-parent merges, 78
tags**, and the last released window is six merges to one release.

What is invariant is the other direction: a release has all four parts. An
invariant stated one step too wide is one a reader checks once, finds false,
and stops checking.

**And the digit decides what the release PROVES**, since 2026-09-20. `X.Y.Z`:
a **Z** publishes the upgrade artifact and no medium, and proves itself with
`make test` alone; an **X** or **Y** publishes the medium and goes through the
whole gate. A Z may change only what the artifact carries, so a change to the
kernel, the VPP or FRR pins, the ISO build, the partitioning or the bootloader
is a Y whatever its size. The four parts above are unchanged by this: a Z is
tagged like anything else and published like anything else — as a
**pre-release**, so that GitHub leaves *Latest* on the last medium this
project booted. What changes is what the published Release carries. `docs/build-timing.md` has the
measurement the rule came from.

*Authoritative:* [CONTRIBUTING.md](../CONTRIBUTING.md).

### 1.9 The wiki is a usage layer, not a second design

`docs/`, `README.md` and `TESTING.md` are authoritative about the product. The
wiki at `hexarouting.hexanetworks.com.br` says how somebody *uses* it, and
`wiki/` here is its mirror, so it can be read and diffed without leaving the
repository.

**Editing that mirror used to change nothing, and since
[#277](https://github.com/andrediashexa/hexarouting/issues/277) it is how a
page is changed**: a correction is written here, `sh tools/wiki-status.sh`
lists what is drafted and unpublished, and `sh tools/wiki-push.sh <file>`
publishes one page. What is still true is that the edit reaches nobody until it
is pushed - which is the whole of what that issue was about, since the mirror
stood twenty-eight pages ahead of what an operator could open.

The wiki is a **draft**: still being written, and still being corrected against
this repository page by page. The conceptual error it was known for — the
purpose of the project, and how deterministic mode relates to BPA — was found
and corrected; [wiki-audit.md](wiki-audit.md) section 2 records what it was, so
that nobody hunts it again. It is not a source — never answer a question about
the product from it.

Where the two disagree, the repository is right and the wiki page is a defect.
Every task asks whether a page needs writing or changing, and records the
answer on the issue even when it is no.

The wiki is currently being audited against this repository; the brief for
that is [wiki-audit.md](wiki-audit.md).

*Authoritative:* `PADRAO-DOCUMENTACAO.md` for how a page is written,
[CONTRIBUTING.md](../CONTRIBUTING.md) section 1 for when.

### 1.10 What GitHub shows is documentation too

`README.md` is the landing page and for most readers the only page. The
repository's About — description, website, topics — is documentation as well,
and the website is the wiki. Every task asks whether it made any of them wrong.

A README goes stale by describing a **smaller product than the one that now
exists**, which is harder to notice than a false statement.

*Authoritative:* [CONTRIBUTING.md](../CONTRIBUTING.md) section 1.

### 1.11 The issue is the record of the work

Design decisions, measurements **with their conditions**, limits and incidental
findings go back to the issue as they happen. Closing carries the acceptance
criteria ticked, with the evidence. A reader six months later has the issue and
the commit, and the commit says what changed, not why it was decided that way.

*Authoritative:* [CONTRIBUTING.md](../CONTRIBUTING.md) section 2.

## 2. What each part answers

Not what it contains — what question it is the answer to.

| Where | The question |
|---|---|
| `src/plugins/bpa/bpa_pool.h` | Given a subscriber, which public address and port block? And the reverse. |
| `src/plugins/bpa/bpa_session.c`, `bpa_session.h` | What state does a translated flow need, and how is it keyed so two VRFs cannot collide? |
| `src/plugins/bpa/bpa_in2out.c`, `bpa_out2in.c` | What happens to one packet, in each direction. |
| `src/plugins/bpa/bpa_rewrite.h` | The outbound rewrite and the checksum corrections it carries on the session. Where the per-packet cost of a translation lives. |
| `src/plugins/bpa/bpa_handoff.c` | Which worker owns this subscriber, and how do both directions reach the same one? |
| `src/plugins/bpa/bpa_dyn.c` | When blocks are allocated on demand rather than computed. |
| `src/plugins/bpa/bpa_ftp.c`, `bpa_pptp.c`, `bpa_rtsp.h`, `bpa_sip.h` | Protocols that carry an address in their payload. |
| `src/plugins/bpa/bpa_esp.h` | IPsec ESP passthrough: no payload to rewrite, only the question of whose tunnel it is. |
| `src/plugins/bpa/bpa_log.c`, `bpa_log_tcp.c` | What is exported, to whom, and what happens when the collector goes away. |
| `src/plugins/bpa/bpa_audit.c` | Which configuration was in force on the date in the court order, and who put it there. |
| `src/plugins/bpa/bpa.api`, `bpa_api.c` | What a control plane may change, and what it is told when refused. |
| `src/plugins/bpa/bpa_cli.c` | The same, for a human at the VPP prompt. |
| `src/prismash/config.py` | What a candidate configuration is, and what changing it costs. |
| `src/prismash/prismash.py` | The VTY: candidate, `commit`, `rollback`, roles. |
| `src/prismash/system.py` | Everything that is not VPP: FRR, SSH, accounts, SNMP. |
| `ref/bpa_model.py` | The mapping, computed from the specification, as the oracle. |
| `test/` | Arithmetic and refusals, in seconds, with no VPP. |
| `lab/` | The same claims, on packets. |
| `appliance/` | Turning a working program into a box somebody can install. |

## 3. Where to start, by task

| If you are about to… | Read, in this order |
|---|---|
| change the mapping | 1.1, 1.3 · `bpa_pool.h` · `ref/bpa_model.py` · [pool-resize.md](pool-resize.md) · `lab/datapath.sh` |
| add a per-pool setting | 1.7 · `config.py` `Pool.lines` · `bpa.api` · `lab/in-place.sh` |
| add an ALG | 1.4 · `bpa_alg.h` · `bpa_rtsp.h` (TCP) or `bpa_sip.h` (UDP) with `test/test_bpa_sip.c` as the pattern · one sequence-correction mechanism, `bpa_seq.h`, and UDP needs none of it |
| touch logging or the trail | 1.2 · [logging.md](logging.md) · `lab/audit.sh`, `lab/ipfix.sh` |
| change the VTY | 1.6, 1.7 · `prismash.py` · `test/test_prismash_complete.py` · `lab/vty.sh` |
| work on the appliance | 1.5 · [appliance.md](appliance.md) · `appliance/prisma-routing-install` · [#83](https://github.com/andrediashexa/hexarouting/issues/83) |
| measure performance | `lab/throughput.sh` · and note that no figure is trusted without hardware, [#28](https://github.com/andrediashexa/hexarouting/issues/28) |
| ask which of the plugin CLI's messages any box ever printed | `LAB_OUTPUT=<dir> sh tools/run-all-labs.sh`, then `sh tools/plugin-says.sh src/plugins/bpa/bpa_cli.c <dir>` · the corpus is deleted with the run unless `LAB_OUTPUT` names somewhere to keep it, and the reader's own halves are driven in `test/test_plugin_says.sh` · it reads three bodies - the `vlib_cli_output` format string, every literal passed as an argument to one, and the `format()` sites `show bpa pool` and `show bpa config` are built from - and `PLUGIN_PRINTERS` narrows that back |
| ask whether it leaks | `lab/soak.sh` · run it deliberately, `sh tools/lab.sh soak 28800`; it is outside `tools/run-all-labs.sh` and outside `make test` because of its length, and what it can see is its margin over the sessions the run churned |

## 4. Deliberately absent — do not report these as gaps

* **High availability.** Built, and the lab that was written first is what
  says so. [ha.md](ha.md) records why the lab came before the implementation
  ([#9](https://github.com/andrediashexa/hexarouting/issues/9)): written
  afterwards it would have been shaped by what was built instead of by the
  requirement. Every check in `lab/ha-failover.sh` was red when it was
  written; all 22 pass now, and it runs in `tools/run-all-labs.sh` like any
  other lab. **Sessions are still not synchronised** - connections drop across
  a takeover and the lab asserts that too, so a green run cannot be read as
  "connections survived".
* **Throughput figures.** Nothing is claimed, and the reason is stronger than
  the one that used to be here. `lab/throughput.sh` runs once, keeps no
  repeats and reports no spread, so **how small a change it could tell from
  run-to-run variation has never been measured** - the lab says as much itself,
  that there is no target to compare against yet. This section used to justify
  the silence with "the bench resolves differences above roughly 3%", a figure
  that appears nowhere else: not in the lab, not in
  [#28](https://github.com/andrediashexa/hexarouting/issues/28), not in the
  changelog ([#325](https://github.com/andrediashexa/hexarouting/issues/325)).
  Publishing no number was right; supporting it with an unmeasured one was the
  same mistake in miniature.
  [#28](https://github.com/andrediashexa/hexarouting/issues/28) is the harness
  that would settle it, and it needs real NICs.
* **Per-flow logging.** Refused. It is the thing BPA exists to avoid.
* **NPTv6, and DPI beyond what an ALG must rewrite.** Out of scope by decision;
  [roadmap.md](roadmap.md) section 4.
* **A graceful pool migration.** Deliberately not offered: it would mean two
  mappings for one subscriber, and the reverse arithmetic could not tell them
  apart — giving up the property deterministic mode exists to provide.
* **An IANA enterprise number.** The SNMP enterprise MIB stays dark until a
  maintainer registers one and sets `snmp enterprise-number`.
* **A custom-built kernel.** Refused with the analysis, in
  [appliance.md](appliance.md) — the appliance would stop receiving Debian's
  kernel security updates, and nothing on the packet path is the kernel's
  ([#163](https://github.com/andrediashexa/hexarouting/issues/163)).
* **The boot-tuning knobs that are listed and not applied.**
  `nmi_watchdog=0`, `skew_tick=1`, `tsc=reliable`, the writeback workqueue mask
  and the rest are recorded in [appliance.md](appliance.md) as hypotheses with
  costs, and `test/test_kernel_tuning.py` asserts none of them is set. They
  wait on [#28](https://github.com/andrediashexa/hexarouting/issues/28), one
  knob at a time
  ([#163](https://github.com/andrediashexa/hexarouting/issues/163)).

## 5. How this page is kept honest

A map that names files which no longer exist is worse than no map: it is a map
that is confidently wrong, which is the failure mode this whole project is
organised against.

`test/test_context_map.py` reads this file, extracts every repository path it
names, and fails when one does not exist. It also counts the invariants below
and requires the TL;DR, the heading over them and `README.md` to say the same
number in words, and requires `README.md` to link here at all. It runs in
`make test`.

It does **not** check that the prose is still true — nothing can. What it
catches is the half that rots silently.
