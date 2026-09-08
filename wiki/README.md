# The wiki, mirrored

A read-only copy of <https://hexarouting.hexanetworks.com.br>, so the pages can
be read, searched and diffed from inside the repository.

Snapshot taken 2026-09-08T11:00Z, 31 pages.

Refresh it with `WIKI_HOST=<host> sh tools/wiki-pull.sh`. No address or
credential is kept here — `PADRAO-DOCUMENTACAO.md` says those never go into the
wiki, and the same applies to the repository that mirrors it.

## What this is not

**It is not where the wiki is edited.** Outline is the live copy and the one
people read; a file changed here is overwritten by the next pull, silently.

**It is not a second source of truth about the product.** `docs/`, `README.md`
and `TESTING.md` are authoritative — the wiki is a usage layer on top of them,
which `PADRAO-DOCUMENTACAO.md` states and this mirror does not change. Where the
two disagree, the repository is right and the wiki page needs updating.

That distinction is the whole risk of having this directory at all: a mirror
that starts being treated as the design is a second copy that diverges from the
first, and nobody can tell which one is stale.

## What is here

| Category | For a reader arriving with… |
|---|---|
| `comecar-aqui/` | a machine to put into service |
| `operar/` | a machine already in production |
| `referencia/` | a specific command to look up |
| `entender-e-desenvolver/` | the question *why is it like this* |
