# HexaRouting — Context model

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
| **What** | The orientation needed to read a 12,000-line dataplane, a 4,400-line VTY and 35 labs without reconstructing the shape first. |
| **Why** | The design is recorded, in sixteen places, each written for a reader who already knows why that file exists. None of them is written first. |
| **Who** | Anyone starting an analysis — a new maintainer, a reviewer, or a model. |
| **Where** | Here; every claim points at the file that owns it. |
| **When** | Before the first question. Several pieces of work in this repository started from an assumption one of the invariants below would have corrected. |
| **How** | Invariants, then a map, then a reading order. |
| **How much** | One page. Anything longer is a second architecture document. |

## 1. The nine things that are not obvious from any one file

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
in force. That is what `bpa_audit.c` exists for, and why it is append-only,
`fsync`ed, carries the whole definition rather than a diff, and now carries the
operator who made the change.

A trail entry that cannot be attributed **MUST** say so; omitting the field
would be indistinguishable from an entry where nobody was accountable.

*Authoritative:* [logging.md](logging.md#configuration-trail), `bpa_audit.c`.

### 1.3 There are two specifications of the mapping, and they MUST agree

`src/plugins/bpa/bpa_pool.h` is the implementation. `ref/bpa_model.py` is the
**executable specification**: it computes the same mapping from the
specification rather than from the implementation, and `make test` checks
30,000+ vectors against it. Any divergence **MUST** break the build.

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

### 1.5 The management interface MUST NOT be given to VPP

DPDK binds a NIC away from Linux. Hand over the port you administer the box
through and it is unreachable, and unreachable is unfixable remotely. On the
lab machines this is `ens18`.

*Authoritative:* [appliance.md](appliance.md), `lab/hw-lab.sh`.

### 1.6 The VTY reaches the dataplane through vat2 — one process per call

Not a persistent connection, and not `vppctl` text. This one fact decides more
than it looks: it is why an identity has to travel *inside each API message*
(there is no connection to attach it to), and why the transport — shared
memory — carries no peer credentials the plugin could check that identity
against.

*Authoritative:* `src/hexash/vppapi.py`, `bpa.api`.

### 1.7 Some settings rebuild the pool and some do not, and the split is load-bearing

Changing a prefix, block size, port range, policy, mode or VRF changes the
mapping, so the pool is rebuilt and every session on it ends. Everything else —
timeouts, ALG switches, bounds, the MSS clamp, bypass lists, forwards — is
applied **in place** on a pool that is carrying traffic.

Getting this wrong is wrong in both directions: a mapping field classified as
in-place never reaches the dataplane; an in-place field classified as mapping
disconnects a subscriber base to turn one ALG off.

*Authoritative:* `Pool.lines(in_place=)` and `Config.delta_to` in
`src/hexash/config.py`; `test/test_hexash_config.py` pins it, including an
assertion that every field a pool has is classified.

### 1.8 Every merge into `master` is a release

Version bump, annotated tag, CHANGELOG entry, published GitHub Release. One
issue per feature, one branch per issue, sub-issues cut from the parent's
branch. The project stays on `0.x.x`, so breaking changes are permitted and are
called out rather than discovered.

*Authoritative:* [CONTRIBUTING.md](../CONTRIBUTING.md).

### 1.9 The wiki is a usage layer, not a second design

`docs/`, `README.md` and `TESTING.md` are authoritative about the product. The
wiki at `hexarouting.hexanetworks.com.br` says how somebody *uses* it, and
`wiki/` here is a read-only mirror so it can be read and diffed without leaving
the repository — editing that mirror changes nothing.

The wiki is a **draft**: still being written, and known to contain errors about
the purpose of the project and about how deterministic mode relates to BPA. It
is not a source — never answer a question about the product from it.

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
| `src/plugins/bpa/bpa_handoff.c` | Which worker owns this subscriber, and how do both directions reach the same one? |
| `src/plugins/bpa/bpa_dyn.c` | When blocks are allocated on demand rather than computed. |
| `src/plugins/bpa/bpa_ftp.c`, `bpa_pptp.c`, `bpa_rtsp.h` | Protocols that carry an address in their payload. |
| `src/plugins/bpa/bpa_esp.h` | IPsec ESP passthrough: no payload to rewrite, only the question of whose tunnel it is. |
| `src/plugins/bpa/bpa_log.c`, `bpa_log_tcp.c` | What is exported, to whom, and what happens when the collector goes away. |
| `src/plugins/bpa/bpa_audit.c` | Which configuration was in force on the date in the court order, and who put it there. |
| `src/plugins/bpa/bpa.api`, `bpa_api.c` | What a control plane may change, and what it is told when refused. |
| `src/plugins/bpa/bpa_cli.c` | The same, for a human at the VPP prompt. |
| `src/hexash/config.py` | What a candidate configuration is, and what changing it costs. |
| `src/hexash/hexash.py` | The VTY: candidate, `commit`, `rollback`, roles. |
| `src/hexash/system.py` | Everything that is not VPP: FRR, SSH, accounts, SNMP. |
| `ref/bpa_model.py` | The mapping, computed from the specification, as the oracle. |
| `test/` | Arithmetic and refusals, in seconds, with no VPP. |
| `lab/` | The same claims, on packets. |
| `appliance/` | Turning a working program into a box somebody can install. |

## 3. Where to start, by task

| If you are about to… | Read, in this order |
|---|---|
| change the mapping | 1.1, 1.3 · `bpa_pool.h` · `ref/bpa_model.py` · [pool-resize.md](pool-resize.md) · `lab/datapath.sh` |
| add a per-pool setting | 1.7 · `config.py` `Pool.lines` · `bpa.api` · `lab/in-place.sh` |
| add an ALG | 1.4 · `bpa_alg.h` · `bpa_rtsp.h` and `test/test_bpa_rtsp.c` as the pattern · [#48](https://github.com/andrediashexa/hexarouting/issues/48) first |
| touch logging or the trail | 1.2 · [logging.md](logging.md) · `lab/audit.sh`, `lab/ipfix.sh` |
| change the VTY | 1.6, 1.7 · `hexash.py` · `test/test_hexash_complete.py` · `lab/vty.sh` |
| work on the appliance | 1.5 · [appliance.md](appliance.md) · `appliance/hexarouting-install` · [#83](https://github.com/andrediashexa/hexarouting/issues/83) |
| measure performance | `lab/throughput.sh` · and note that no figure is trusted without hardware, [#28](https://github.com/andrediashexa/hexarouting/issues/28) |

## 4. Deliberately absent — do not report these as gaps

* **High availability.** Analysed, not implemented; [ha.md](ha.md) says why the
  lab comes before the implementation
  ([#9](https://github.com/andrediashexa/hexarouting/issues/9)).
* **Throughput figures.** Nothing is claimed. The bench resolves differences
  above roughly 3%, which is enough to catch a regression and not enough to
  publish a number
  ([#28](https://github.com/andrediashexa/hexarouting/issues/28)).
* **Per-flow logging.** Refused. It is the thing BPA exists to avoid.
* **NPTv6, and DPI beyond what an ALG must rewrite.** Out of scope by decision;
  [roadmap.md](roadmap.md) section 4.
* **A graceful pool migration.** Deliberately not offered: it would mean two
  mappings for one subscriber, and the reverse arithmetic could not tell them
  apart — giving up the property deterministic mode exists to provide.
* **An IANA enterprise number.** The SNMP enterprise MIB stays dark until a
  maintainer registers one and sets `snmp enterprise-number`.

## 5. How this page is kept honest

A map that names files which no longer exist is worse than no map: it is a map
that is confidently wrong, which is the failure mode this whole project is
organised against.

`test/test_context_map.py` reads this file, extracts every repository path it
names, and fails when one does not exist. It runs in `make test`.

It does **not** check that the prose is still true — nothing can. What it
catches is the half that rots silently.
