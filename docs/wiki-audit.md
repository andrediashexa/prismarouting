# Auditing the wiki against the product

## TL;DR

The wiki at <https://hexarouting.hexanetworks.com.br> is a **draft**. It is not
the base of truth, it is still being written, and the maintainer has said it
contains **errors about the purpose of the project — including about
deterministic CGNAT and its relationship to BPA**.

This page is the brief for the session that goes and fixes that. It assumes no
other context.

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
| **How much** | 31 pages, four of them already corrected — see below. |

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

Four pages were updated on 2026-09-08 against v0.32.0:

| Page | What was wrong or missing |
|---|---|
| *Roteamento, SSH, usuários e SNMP* | taught `show ip route`, which answers from FRR's RIB — the table where a route looks correct on a box that is dropping the traffic. Static routes and the daemon question were missing. |
| *Comandos show* | no `show route`, `show route verbose` or `show this` |
| *Instalação da appliance* | no records volume, no `hexarouting.log-gb=` |
| *Build, testes e laboratórios* | no parallel suite, no `make release-gate` |

Those four were corrected for **content that was stale**. None of them was
audited for the conceptual error in section 2 — that is this brief's job.

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
