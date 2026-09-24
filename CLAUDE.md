# How this repository is worked on

Read this first. It is the entry point: what the rules are, where each one is
written down, and what is true here and written nowhere else. It does **not**
repeat the rules — it points at the document that owns each, and names the
check that refuses a violation, because in this repository a rule with no
reader behind it is a comment.

> The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
> are to be interpreted as described in
> [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## The product, in three sentences

Prisma Routing is an installable Debian appliance: a VPP/DPDK dataplane with a
CGNAT that allocates a **port block** per subscriber (BPA), FRR for routing,
and one VTY — `prismash` — for both. It is sold to ISPs, it carries
subscribers, and the thing it is judged on is whether an operator can trust
what the box says about itself. `README.md` has the product; `docs/context.md`
has the eleven things that are not obvious from any single file.

## How the work goes

**One issue at a time, in the order `docs/roadmap.md` gives.** Finish it, take
it through a release, close it with a comment saying what was delivered and
what was found, then read the backlog and take the next. `docs/handoff.md` is
the state of play at the end of the last session — what shipped, what is on a
branch waiting, what is decided and not started.

**Every piece of work is an issue**, opened before the work and closed after
the release that carries it. The issue is the record: what was measured, what
was decided, and why the other options were rejected. `CONTRIBUTING.md` §2.

**A branch per issue**, named for what it does rather than for its number
(`fix/a-loopback-is-visible-to-frr`), cut from `master`. `CONTRIBUTING.md` §3.

**Each merge into `master` is a release.** `CONTRIBUTING.md` §5 is the
procedure and the X.Y.Z rule; the checklist below is that procedure as steps.
In short: **Z** changes only what the upgrade artifact carries (or what ships
nowhere — `test/`, `lab/`, the contributor documents) and is published as a
**pre-release** with no ISO, proven by `make test`; **X** or **Y** publishes
the medium and goes through the whole gate.

## The rules that have teeth

Each of these is enforced by something in `make test` or by the release gate.
The document owns the rule; the reader refuses the violation.

| Rule | Written in | Refused by |
|---|---|---|
| Every function has a unit test, or says at its definition why it cannot be | `TESTING.md` | `test/test_every_function.py` |
| Every refusal the VTY prints fits a 79-column console, at its widest value | `TESTING.md` | `test/test_prismash_refusals.py`, `test/test_console_width.py` |
| Every command and every screen quoted in a document is one the box produces | `TESTING.md` | `test/test_doc_commands.py`, `test/test_doc_copies.py`, `test/test_wiki_screens.py` |
| The `CHANGELOG` entry lands in the section that is still open | `CONTRIBUTING.md` §5 | `test/test_changelog_landing.py` |
| The roadmap and the issue tracker agree about what is open | `CONTRIBUTING.md` §1 | `tools/roadmap-state.sh`, and the gate |
| The published wiki matches the mirror in `wiki/` | `CONTRIBUTING.md` §1 | `tools/wiki-status.sh`, and the gate |
| A published release title is its `CHANGELOG` heading, version and all | `CONTRIBUTING.md` §5 | `tools/release-titles.sh`, `test/test_release_titles.sh` |
| Documents in this repository are in English; the wiki is in Portuguese | `CONTRIBUTING.md` §1 | `test/test_english_only.py` |
| No credential is shipped inside the medium | `docs/appliance.md` | `test/test_no_credentials.sh` |
| A release ships a medium that was built, booted, installed and booted again | `CONTRIBUTING.md` §5 | `tools/release-gate.sh` |

**When a check refuses you, the check is usually right.** Every one of them was
written after something reached a released medium. Read what it says before
deciding it is wrong — and if it is wrong, fix the check in the same branch and
say so in the commit.

## The commands

```sh
make test                  # every unit suite; no root, no VPP, about two minutes
sh tools/lab.sh            # list the dataplane labs
sh tools/lab.sh vty        # run one, in docker
sh tools/run-all-labs.sh   # the whole lab suite, about twenty minutes
sh tools/vty.sh            # open the VTY against a local dataplane
sh tools/release-gate.sh v0.109.0   # the release procedure, about 59 minutes
sh tools/roadmap-state.sh  # does the roadmap agree with the tracker
sh tools/wiki-status.sh    # is anything drafted here and not published
```

`TESTING.md` says what each level proves and what it does not.
`docs/build-timing.md` says where the gate's 59 minutes go — about 30 seconds
of it is compiling this product, so do not optimise the compile.

## Cutting a release, as steps

`CONTRIBUTING.md` §5 is the rule; this is the order the steps go in, because
two of them have bitten when done late.

1. The work is on its branch, `make test` is green, and the labs that touch it
   have been run.
2. **Publish the wiki, pull it back, and commit the pull — before the tag.**
   `WIKI_TOKEN=... sh tools/wiki-publish.sh`, then
   `WIKI_PULL_DISCARD=1 sh tools/wiki-pull.sh`, then commit. The gate compares
   the mirror against the commit `wiki/.pulled` names, so a page published
   while that pull is uncommitted reads as unpublished — and the refusal
   arrives twenty minutes into a build.
3. Merge into `master` with `--no-ff`.
4. Bump `VERSION`, rename `## Unreleased` to `## X.Y.Z - <title>` and leave an
   empty `## Unreleased` above it, with its `### Added` and `### Fixed`.
5. Annotated tag `vX.Y.Z`, whose message **is** the release note. For an X or Y
   release it MUST contain, in one paragraph, that the medium was *built,
   booted, installed to a disk and booted again, and upgraded from the previous
   release*. A Z release MUST NOT claim that: it did none of it.
6. `sh tools/release-gate.sh vX.Y.Z`, detached (see below). It reads which
   digit moved and takes the matching path: about 59 minutes for an X or Y,
   minutes for a Z — which builds no medium and says so.
7. `git push origin master --follow-tags`, then `gh release create`. The title
   is the `CHANGELOG` heading, version included.
   * **X or Y**: a full release, with the **four** assets — the ISO, the
     upgrade artifact and a `sha256` for each.
   * **Z**: `--prerelease`, with the artifact and its `sha256`. Tagged like any
     other release; pre-release so that **Latest** stays on the last medium
     this project actually booted.

   Then `sh tools/publish-release.sh vX.Y.Z`, which puts the same files on
   the **public** releases directory, reads them back from the public URL and
   moves `latest` forward. This repository is private, so the GitHub release
   is a page no operator can open: the directory is where the wiki sends them
   to download ([#439](https://github.com/andrediashexa/hexarouting/issues/439)).
   The upload target is `~/.releases-target`, one line, `user@host:/path`, and
   like the wiki token it is not in this repository.
8. Close the issue with a comment: what was delivered, what was measured, and
   anything found on the way that is not in the CHANGELOG.

## Every commit is announced

**A commit that lands is announced on Discord**, by the maintainer's rule of
2026-09-20. It is a `post-commit` hook rather than a step somebody remembers,
because a step somebody remembers is a step that is eventually skipped:

```sh
ln -sf ../../tools/git-hooks/post-commit "$(git rev-parse --git-common-dir)/hooks/post-commit"
```

One link covers every worktree of this repository, since they share one `.git`.
It sends the subject, the short hash, the branch and the version — never the
diff — and it **cannot fail a commit**: `tools/announce-commit.sh` returns 0
whether Discord answers, refuses, or is not configured at all.

**The webhook URL is a credential and is NOT in this repository.** It lives in
`~/.discord-webhook`, one line, mode 600, or in `DISCORD_WEBHOOK`. The reason
is not theoretical: `appliance/build-iso.sh` copies `docs/` into the medium, so
a webhook written into a document would be published inside every ISO — which
is the defect `test/test_no_credentials.sh` exists to prevent. It is the same
arrangement `~/.wiki-token` already has.

An amend is a new commit object and is announced again, with its own hash.
That is deliberate: the alternative is a rule about which commits count.

## Conventions that are written nowhere else

* **Build caches live in `/var/cache/prisma`, never in the tree.** The gate
  clones the tag into a fresh directory and deletes it first, so a cache inside
  the repository is one the release procedure destroys before it can be hit —
  which is what has happened to `.build-old/<tag>` for its whole life.
* **Worktrees, one per branch.** Work happens in `/home/hexanetworks/wt-<short>`,
  created with `git worktree add`. There are several hundred; they are cheap and
  nobody prunes them.
* **Detach anything long.** A lab or a gate started from a shell that returns is
  killed with it. Use
  `setsid sh -c '<command> > <log> 2>&1' < /dev/null > /dev/null 2>&1 &` and poll
  the log.
* **Never chain an edit with a commit** in one command. A conflicted merge was
  committed that way once, and the mistake is invisible until somebody reads
  the history.
* **The maintainer tests on a real box** and reports what he sees, often with a
  screenshot. Five of the nine releases cut on 2026-09-19 came from that, not
  from a suite. When a screenshot arrives, reproduce it on the box or in a lab
  before deciding what it means — twice the obvious reading was wrong.
* **Comments carry the measurement, not the intention.** The house style is to
  write down what was measured, on what, and what it refuted. A comment saying
  what the code does is noise; a comment saying why the obvious version of it
  was wrong is the reason the next person does not redo it.
* **A lab that asserts what the box does WRONG is worse than no lab.** Two were
  found doing exactly that in one day (`lab/routing.sh`, `lab/subif.sh`): they
  pinned the defective behaviour as ordinary, so the suite stayed green while
  an operator hit it.

## Where to start reading, by task

| Task | Read |
|---|---|
| Anything at all, first time | `docs/context.md`, then `README.md` |
| The VTY, the commit model, interfaces, routing | `docs/routing.md` |
| The appliance, the installer, the ISO, tuning | `docs/appliance.md` |
| CGNAT internals, the mapping, memory | `docs/architecture.md`, `ref/bpa_model.py` |
| Logging, IPFIX, syslog, the identity in a record | `docs/logging.md` |
| High availability, VRRP, the pair | `docs/ha.md` |
| What to do next | `docs/roadmap.md`, then `docs/handoff.md` |
| How a wiki page is written | `PADRAO-DOCUMENTACAO.md` |
| Why a release takes 59 minutes | `docs/build-timing.md` |
