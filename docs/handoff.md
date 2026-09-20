# Where this stands, for whoever picks it up next

Written 2026-09-20, at the end of a long session, so the next one starts with
the state rather than reconstructing it. **This file is a handoff, not a plan**
— `docs/roadmap.md` is the plan, and where the two disagree the roadmap is
right. `CLAUDE.md` is how this repository is worked on; read that first.

## Released in that session

| | | |
|---|---|---|
| 0.101.0 | `top`, `up [<n>]` and the probes, at every configuration level | #400 |
| 0.102.0 | a published release title carries its version, and a suite drives the check | #402 |
| 0.103.0 | the interface screens list what an operator configured, not the TAPs and `local0` | #403 |
| 0.104.0 | a loopback is paired into Linux by default, so FRR can see it | #404 |
| 0.105.0 | `?` says which names create a sub-interface or a loopback | #406 |
| 0.106.0 | a card the box has and the dataplane has not: three screens say the same true thing | #405 |
| 0.107.0 | a sub-interface under a shut-down parent no longer fails every commit | #407 |
| 0.108.0 | a release record repeats what its allocation said | #397 |
| 0.109.0 | where the release time goes, and the X.Y.Z rule that follows | #408, #409, #410 |
| 0.109.1 | `CLAUDE.md`, and every commit is announced on Discord | — |
| 0.109.2 | the gate knows which digit moved | #410 |

Everything up to 0.109.0 went through the full gate with an ISO. **0.109.1 and
0.109.2 are the first two patch releases**: no medium, published as
pre-releases, so `Latest` on the releases page is still 0.109.0 — the last
medium this project actually booted.

Five of those were defects the maintainer found by **using** a box. That is the
pattern worth carrying: the suites are green and the box is where the next one
comes from.

## Ready to merge, on a branch, green

Both were finished and tested before the X.Y.Z work interrupted them. Neither
has been rebased since 0.109.2.

* **`docs/the-collector-retains-not-the-box` (#401)** — a **minor**: it changes
  the installer, which exists only on the medium. The whole sweep is written
  and `make test` is green on it. Every document, screen, self-test line, test
  needle and wiki page now says the box **exports** its records and keeps none;
  the log volume holds the configuration trail and the exporter's spill. The
  wiki pages are published and pulled. Needs a rebase, a bump to 0.110.0 and
  the full gate.
* **`issue/390-rendering-excused` (#390 step 1)** — a **patch**: it touches
  only `bpa_cli.c` comments, `test/` and `docs/`. The forty-one rendering
  functions carry `not a unit:` with the class each belongs to, the argument
  made once in that file's header; the C ceiling drops 171 → 130, and the
  reader takes the reason to be the whole comment block rather than the rest of
  one line. Rebase, bump, and the gate's patch path — about three minutes.

## Decided and not started

In the order the roadmap gives:

1. **#210** — the per-worker partition. Option 2 is chosen (the partition
   follows the hash) and the mechanism is settled in a comment on the issue: a
   borrower cannot write the lender's slice, so the move happens under the
   barrier `bpa_expire_process` already takes, with a lent-block set the
   `out2in` **miss** path consults. Four pieces, three assertions, in that
   comment. Nothing is written yet, and it is the largest thing in the backlog.
2. **#408** — a clock on every step of the release, on its own and before any
   optimisation. `docs/build-timing.md` says exactly where it goes. The
   maintainer chose that order so the optimisations aim at measured numbers.
3. **#409** — the versioned base rootfs in `/var/cache/prisma`, the dev image
   holding its own tools, and `.build-old/<tag>` moved out of the directory the
   gate deletes. One unfinished piece belongs here: the gate prints
   `gh release create --prerelease` but does not publish, so nothing yet
   refuses a patch published as a full release.
4. **#396 phase 1** and **#337**, the two the maintainer had already answered
   and neither has been started.

## What is worth knowing before touching anything

* **Publish the wiki, pull it and commit the pull *before* the tag.** The gate
  refused two releases in one day for shipping behind their own wiki, both
  times after twenty minutes of building. `CONTRIBUTING.md` says so now.
* **A minor release is 36 minutes; a patch is three.** About 30 seconds of
  either is compiling this product — do not optimise the compile.
  `docs/build-timing.md` has the measurement.
* **The labs are where the interesting failures are found.** #407's fix was
  wrong twice and both times a lab said so, not a unit test — and `lab/subif.sh`
  and `lab/routing.sh` had each been *asserting* the defective behaviour as
  ordinary, which is how it reached a maintainer's box with a green suite
  behind it.
* **Two checks were wrong about trees rather than about the product**, and both
  surfaced only when the gate started running `make test` inside its own clone:
  the CHANGELOG comparison took the newest *other* tag instead of the tag
  below, and `test/test_installer.sh` could not run from a tree under `/tmp` —
  which is where the gate clones. Expect more of that shape wherever a check
  has only ever been run from one place.
* **`tools/lab.sh <name>` runs one lab**; the suite is `tools/run-all-labs.sh`
  and it takes about twenty minutes. A lab launched from a shell that returns
  is killed with it — use `setsid` to detach it.
