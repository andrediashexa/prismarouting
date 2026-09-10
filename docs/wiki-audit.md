# Auditing the wiki against the product

## TL;DR

The wiki at <https://hexarouting.hexanetworks.com.br> is a **draft**. It is not
the base of truth, it is still being written, and the maintainer has said it
contains **errors about the purpose of the project — including about
deterministic CGNAT and its relationship to BPA**.

This page is the brief for the session that goes and fixes that. It assumes no
other context.

**The conceptual error named below was found and corrected on 2026-09-09** —
see section 2, *Found and corrected*. Read that before hunting for it again.
What remains is the ordinary work: checking the rest of the pages, claim by
claim, against a repository that has moved several releases since.

> The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
> are to be interpreted as described in
> [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## 5W2H

| | |
|---|---|
| **What** | Check every wiki page against this repository and correct what disagrees. |
| **Why** | The wiki is public. A page that is wrong about the product is wrong in front of customers, partners and integrators. |
| **Who** | Whoever takes the audit; it is deliberately a separate piece of work. |
| **Where** | Outline, edited through its API. The mirror under `wiki/` is for reading. |
| **When** | Before the wiki is treated as documentation by anybody. |
| **How** | Claim by claim against `docs/`, never the reverse. |
| **How much** | 29 pages. Two passes already done — see section 5. |

## 1. What is authoritative, and what is not

**This repository is authoritative about the product.** `docs/architecture.md`,
`docs/logging.md`, `docs/routing.md`, `docs/appliance.md`, `docs/pool-resize.md`,
`README.md` and `TESTING.md` say what the software does and why.
`ref/bpa_model.py` is the executable specification of the mapping.

**The wiki is a usage layer on top of that, and currently a draft.** When a page
and `docs/` disagree, the page is a defect. A claim **MUST NOT** be checked
against another wiki page — several of them are wrong in the same direction,
and agreeing with each other is what wrong pages do.

`wiki/` in this repository is a **read-only mirror**, refreshed with
`WIKI_HOST=<host> sh tools/wiki-pull.sh`. Editing a file there changes nothing:
the next pull overwrites it, and Outline is where a page is actually edited.

## 2. The error to hunt

The maintainer named it: **the purpose of the project, and deterministic CGNAT
versus BPA**.

The distinction the repository makes, and the one to check every page against:

* **BPA** (*Bloco de Portas Alocadas*) is the **technique** — a contiguous
  block of ports per subscriber, instead of a record per flow.
* **Deterministic** and **dynamic** are the two **modes within BPA**:
  * *deterministic* — the public address and port block are **computed** from
    the pool definition. There is no allocation event, so there is nothing to
    export; the proof offered to an authority is *the algorithm plus the
    configuration in force at that instant*, which is why the configuration
    trail exists.
  * *dynamic* — the block is allocated on demand, with one IPFIX record per
    block (RFC 8158), not per flow.

They are **not alternatives to BPA and not alternatives to each other at the
same level**. A page that presents "BPA" and "CGNAT determinístico" as two
choices has the shape of the error.

*Authoritative:* [architecture.md](architecture.md),
[logging.md](logging.md), `ref/bpa_model.py`.

Note that the two pages most likely to carry it — *HexaRouting — visão geral*
and *BPA — conceitos e mapeamento* — were read during the mirror import and
**appeared correct**: both present deterministic and dynamic as modes of BPA.
So either the error is elsewhere, or it is subtler than those two openings. Do
not assume it has been found because those two read well.

### Found and corrected, 2026-09-09

The suspicion above was right: it was subtler than the openings, and it was
**one label**. The mode table on *BPA — conceitos e mapeamento* read

```
|     | Determinístico | Dinâmico (PBA) |
```

which puts PBA on the dynamic column alone. A reader takes from that exactly
the shape section 2 describes — that PBA is the dynamic thing and that
deterministic is therefore not block allocation, so the two are different
techniques rather than two modes of one.

That is now:

* the section is titled **"BPA é a técnica; determinístico e dinâmico são os
  modos dela"** and says both modes allocate by block, that what differs is
  *how* the block is assigned, and that the choice is between the two modes —
  never between "BPA" and one of them;
* the columns read **Modo determinístico** and **Modo dinâmico**; the string
  "PBA" no longer appears on any page.

The **home** carried the cause rather than the error: the reader met
"Determinístico" and "Dinâmico" for the first time as table headings, with no
sentence introducing them as the two modes of BPA. A section **"Os dois modos
do BPA"** now precedes that table.

## 3. Access

The wiki runs Outline in Docker on a machine on the management network, with
Postgres beside it. **No address, password or token is kept in this
repository** — `PADRAO-DOCUMENTACAO.md` says credentials and management
addresses never go into the wiki, and the same applies to the repository that
mirrors it. Ask the maintainer for the host and login.

### Reading

Prefer the mirror: `WIKI_HOST=<host> sh tools/wiki-pull.sh`, then read `wiki/`.
It is markdown, greppable, and diffable against the last pull.

### Writing

**Pull first, then push with `tools/wiki-push.sh`.** It refuses to publish over
a document that changed after the mirror was pulled. The wiki is being written
by people right now — a category was renamed and two pages removed during the
work that produced this brief — and an edit built from an old snapshot
discards whoever made the change in between.

**Through the API, never the database.** Outline keeps revisions, a search
index and collaborative state; writing document rows underneath it corrupts all
three silently.

An API key named `hexarouting-docs-sync` already exists for the admin account.
Its secret is not stored anywhere — if it is lost, make another: Outline hashes
the token with SHA-256 and stores `hash` and `last4` in the `apiKeys` table, and
the token is `ol_api_` followed by 38 word characters. Generate one, insert the
row with its hash, and the API accepts it. Verify with:

```sh
curl -s -X POST https://hexarouting.hexanetworks.com.br/api/auth.info \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
```

To update a page, POST to `/api/documents.update` with `id`, `text` and
`publish: true`. Document ids come from the pull, or from
`/api/documents.list`.

## 4. How to write a page

`PADRAO-DOCUMENTACAO.md` governs the wiki and is **not** the same standard as
`CONTRIBUTING.md`. Read it before writing. In short:

* Every page opens with one to three sentences saying what it solves and for
  whom — no dictionary definition, no product history.
* A page is a **task**, a **reference** or a **concept**, and does not mix the
  three. Tasks close with *Como conferir*; the others with *Ver também*.
* `:::warning` is for things that cost service, data or legal proof. Nothing
  else. `:::tip` is not used at all.
* Diagrams are Mermaid. Never ASCII art.
* **Never invent the output of a command.** Describe what to expect, or quote
  only what you have actually seen.
* Nothing goes in — marked or not — that is a password, key, token, SNMP
  community, management address or internal machine name. **The wiki is public
  on the internet.**

## 5. Already corrected, so it is not redone

### 2026-09-09, against v0.53.0 — listas de AS-path e de comunidade

**Added to *Roteamento, SSH, usuários e SNMP*: "Listas de AS-path e de
comunidade".** A section above the route-map one, because that is the reading
order: a route-map matches on a list, so the list has to exist first.

The page spends most of its length on the failure that **cannot** be refused —
a regex that compiles and matches the wrong thing — because that is the one an
operator meets. It states plainly what `_` means in FRR (any boundary, not an
underscore), since that is the detail that turns a correct-looking regex into a
wrong one, and it tells the reader how to read the match count: much larger
than expected means a missing anchor, zero means the list filters nothing.

The note about an empty table is documented as its own callout rather than as a
footnote: "matched 0" and "there was nothing to match against" are different
answers, and a reader who conflates them goes looking for a bug in their regex.

### 2026-09-09, against v0.52.0 — route-maps, on the policy page

**Added to *Roteamento, SSH, usuários e SNMP*: "Route-maps: a política em si".**
A section rather than a page: the routing page already carries prefix-lists and
the pool-announcement check, and route-maps are the same subject seen from the
other end. A sixth thin subpage would have split policy across two places.

Written around the split the product makes — what is refused, what is only
said, and why a `permit` with no `match` is the second kind. The page says out
loud that the warning is one line of noise when the catch-all was deliberate,
because an operator who reads it as an error will go looking for a bug that is
not there.

The "not checked by this VTY" marker is documented **with its limits**: it is
display only, it does not reach the saved configuration or a rollback revision,
and copying the screen into a file is safe because the marker is stripped on
the way back in. That last property is asserted in the test suite; without it
the sentence would be a promise nobody checked.

### 2026-09-09, against v0.51.0 — BGP has a page

**Added: *Configuração de BGP***, a subpage of the VTY guide, alongside
*Configuração de interface*. The VTY now models the `router bgp` block, and the
wiki covered BGP only as three lines inside the routing page — enough to say
policy exists, not enough to configure a session.

The page is organised the way an operator meets the thing: one session from
start to finish, then the table of what a neighbour takes, then peer groups
(because forty peerings are not forty stanzas), then the refusals, then the
password. The refusals are written as *shapes that cannot work*, and the page
says out loud that whether the neighbour answers is not something the
configuration knows — a page that implies the box can predict a session coming
up is a page that will be blamed when it does not.

Linked from the guide's *Ver também* and from the paragraph that already
pointed at the interface subpage, so it is not an orphan.

**A slug invented again, and caught before publication.** Writing the *Ver
também* links I typed two `/doc/` slugs from memory. Both were wrong. They were
replaced with the real ones read from the wiki, and every internal link on
every page was then checked against the live list: 0 broken. This is the second
time the same mistake was made — the fix is not to be more careful, it is to
never type a slug that was not read back.

### 2026-09-09, against v0.50.0 — a default reversed, and a refusal explained

**Rewritten on *Configuração de interface*: the router-advertisement section.**
The page said an interface with an IPv6 address advertises this box as a
default router every 200 seconds, which was true when it was written and is now
the opposite of what the product does. The default is suppression; `no ipv6 nd
suppress-ra` is the exception written on the ports that should advertise — an
uplink, a transit, a port facing routers. The command table row changed with
it, and the callout that told the reader to suppress on subscriber ports is
gone, because suppressing is no longer something they have to remember.

**Added to *Roteamento, SSH, usuários e SNMP*: the `ge`/`le` refusal.** With the
rule, the error text as the box prints it, and the one thing that is confusing
about it: the prefix comes back normalised, so `10.12.0.0/8` is reported as
`10.0.0.0/8` and searching the configuration for the printed prefix finds
nothing. The reader is told to look up the list by name instead.

The note on why the refusal happens at the VTY rather than further along is
written as consequence, not as history: refused later, it also broke the
*next* commit with the same message, pointing at a prefix that was in no
configuration at all. That is what the operator would have had to diagnose, so
that is what the page explains — with no issue number and no link, per section
0.

### 2026-09-08, against v0.44.0 — the interface block, and a rule broken

**Added: *Configuração de interface***, a subpage of the VTY guide. The
interface block went from two statements to ten across v0.40.0-v0.44.0
(description, shutdown, IPv4 and IPv6 addresses, VLAN and sub-interfaces, VRF,
MAC, IPv6 RA suppression, MTU, uRPF) and the wiki covered none of it. Made a
subpage rather than sections in the guide because the guide already splits by
area, and ten statements would have doubled its length.

The guide's own inline list now points at it instead of growing a second copy.

**Corrected on *Instalação da appliance*: a warning I wrote earlier the same
day cited two issue numbers and linked GitHub.** Section 0 of
`PADRAO-DOCUMENTACAO.md` keeps both off a public page, and a reader outside the
company cannot open either link. The warning itself was right and stays; the
citations are gone, and the fact that the medium is now installed to a disk and
booted before publication is stated as behaviour rather than as an issue
reference.

That rule is now also in `CONTRIBUTING.md`, with the mistake recorded next to
it, because reading the standard once did not stop me breaking it on the first
page I touched.

**Two internal links in the new page were invented rather than looked up**, and
were published broken before being corrected. Every `/doc/` link across all 30
pages was then checked against the live list: zero broken.

**Not verified in a browser.** The standard asks for it and this session has no
browser. Mermaid rendering, CSP and chunk loading are exactly what `curl`
cannot prove, and the new page carries no diagram - but the callouts on it have
not been seen rendered, and the round-trip has broken callouts in half before.
Worth a look by somebody who can open it.



Six pages were updated on 2026-09-08 against v0.32.0 and v0.33.0. One of them,
*Build, testes e laboratórios*, **was removed from the wiki shortly
afterwards** when the category *Entender e desenvolver* became *Entender o
produto* — developer content leaving a wiki that is for operators. Read that as
a warning about direction: the edit was accurate and it was the wrong page.

| Page | What was wrong or missing |
|---|---|
| *Roteamento, SSH, usuários e SNMP* | taught `show ip route`, which answers from FRR's RIB — the table where a route looks correct on a box that is dropping the traffic. Static routes and the daemon question were missing. |
| *Comandos show* | no `show route`, `show route verbose` or `show this` |
| *Instalação da appliance* | no records volume, no `hexarouting.log-gb=` |
| *Build, testes e laboratórios* | no parallel suite, no `make release-gate` |

Those four were corrected for **content that was stale**. None of them was
audited for the conceptual error in section 2 — that is this brief's job.

### Second pass, 2026-09-09 — audience, and the rest of the staleness

The wiki is for whoever **uses** the platform, not for whoever develops it.
That rule is now `PADRAO-DOCUMENTACAO.md` section 0, with the list of what
stays out; it was a decision taken in conversation and written down so it
survives the next session.

Applying it:

| Page | What changed |
|---|---|
| *Build, testes e laboratórios* | **deleted.** Compiling, `make`, the build container, the 40 labs — an operator does none of it, and `README.md` and `TESTING.md` cover it without a second copy that diverges. |
| *Dataplane — comandos vppctl e API* | **deleted.** The binary API is for whoever writes a control plane; the product exists so the operator never leaves the VTY. |
| *Entender e desenvolver* | renamed **Entender o produto**; its opening no longer invites the reader to touch the code. |
| six operator pages | source filenames (`bpa_audit.c`, `bpa_handoff.c`, `bpa.api`, `tools/ipfix_collector.py`) and lab script names removed. The *claim* they supported was kept — "this is verified on the captured packet, not on a counter" is a trust argument for whoever is evaluating the product; naming the script that does it is not. |
| *Instalação da appliance*, *Logging IPFIX* | sections that only served whoever builds the ISO or runs the labs removed. |
| *Operação e diagnóstico*, *Configuração de CGNAT* | **"Nas máquinas de laboratório essa interface é a `ens18`"** removed from both. `PADRAO-DOCUMENTACAO.md` lists internal machine and interface names among the things that never go into a public wiki. |

Stale content corrected in the same pass:

| Page | What was wrong |
|---|---|
| *VTY hexash — guia de uso* | the prompt table predated v0.32.0: it showed `[edit cgnat pool]` without the pool's name and claimed entering an interface moved nothing. Both are the defect [#108](https://github.com/andrediashexa/hexarouting/issues/108) fixed. `show this` and the completion of live values from v0.29.0 were missing. |
| *Dimensionamento e requisitos* | no records volume, no `hexarouting.log-gb=`, no ~29 GB/year arithmetic (v0.30.0) |
| *Operação e diagnóstico* | the boot wait did not explain itself (v0.31.0) |
| *BPA*, *Ampliar capacidade* | a `:::warning` on each held only its first sentence, the rest having fallen outside the block — Outline's markdown round-trip splits a callout whose source was a one-line blockquote. Check this after every edit that touches one. |

**A trap worth not paying twice:** in the `wiki/` mirror, 21 places read
`` `**vppctl**` `` and look like broken markdown. They are not — the live page
renders `<strong><code>vppctl</code></strong>` with no literal asterisks. It is
an artefact of Outline's markdown serialiser on export. Correcting it in the
mirror would corrupt what is already right.

## 6. A method that will not fool you

1. Pull the mirror, so the audit runs against what the wiki says today.
2. Take one claim at a time from a page. For each, find the sentence in `docs/`
   that supports or contradicts it. **A claim with no counterpart in `docs/` is
   not thereby true** — it may be a claim nobody has checked.
3. Where they conflict, the page is wrong. Where `docs/` is silent and the
   claim matters, that is a question for the maintainer, not a licence to keep
   the page as it stands.
4. Record what was changed and why. A page corrected without a reason is a page
   the next reader will change back.

## Related

* [context.md](context.md) — the invariants of the product, and which file owns
  each one. Read it before judging any claim.
* `PADRAO-DOCUMENTACAO.md` — how a wiki page is written.
* [CONTRIBUTING.md](../CONTRIBUTING.md) section 1 — when a task must touch the
  wiki at all.
