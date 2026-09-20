# Where this stands, for whoever picks it up next

Written 2026-09-20, at the end of a long session, so the next one starts with
the state rather than reconstructing it. **This file is a handoff, not a plan**
— `docs/roadmap.md` is the plan, and where the two disagree the roadmap is
right.

## Released today

0.101.0 through 0.109.0, each through the full gate and published with its ISO,
its upgrade artifact and both `sha256`. What they were:

| | |
|---|---|
| 0.101.0 | `top`, `up [<n>]` and the probes, at every configuration level (#400) |
| 0.102.0 | a published release title carries its version, and a suite drives the check (#402) |
| 0.103.0 | the interface screens list what an operator configured, not the TAPs and `local0` (#403) |
| 0.104.0 | a loopback is paired into Linux by default, so FRR can see it (#404) |
| 0.105.0 | `?` says which names create a sub-interface or a loopback (#406) |
| 0.106.0 | a card the box has and the dataplane has not: three screens say the same true thing (#405) |
| 0.107.0 | a sub-interface under a shut-down parent no longer fails every commit (#407) |
| 0.108.0 | a release record repeats what its allocation said (#397) |
| 0.109.0 | where the release time goes, and the X.Y.Z rule that follows (#408, #409, #410) |

Five of those were defects the maintainer found by **using** a box —
10.95.200.198 and the one in the 0.105.0 screenshot. That is the pattern worth
carrying: the suites are green and the box is where the next one comes from.

## Ready to merge, on a branch, green

* **`docs/the-collector-retains-not-the-box` (#401)** — the whole sweep is
  written and `make test` is green on it. Every document, screen, self-test
  line, test needle and wiki page now says the box **exports** its records and
  keeps none; the log volume holds the configuration trail and the exporter's
  spill. The wiki pages are published and pulled. It needs a rebase onto
  master, a version bump and the gate.
* **`issue/390-rendering-excused` (#390 step 1)** — `bpa_cli.c`'s forty-one
  rendering functions carry `not a unit:` with the class each belongs to, the
  argument made once in that file's header; the C ceiling drops 171 → 130, and
  the reader now takes the reason to be the whole comment block rather than the
  rest of one line. Same: rebase, bump, gate.

## Decided and not started

In the order the roadmap gives:

1. **#210** — the per-worker partition. Option 2 is chosen (the partition
   follows the hash) and the mechanism is settled in a comment on the issue: a
   borrower cannot write the lender's slice, so the move happens under the
   barrier `bpa_expire_process` already takes, with a lent-block set the
   `out2in` **miss** path consults. Four pieces, three assertions, in that
   comment. Nothing is written yet.
2. **#408** — a clock on every step of the release, on its own, before any
   optimisation. `docs/build-timing.md` says exactly where it goes.
3. **#409** — the versioned base rootfs, and the dev image holding its own
   tools.
4. **#410** — the gate learning which digit moved, which is what makes the
   X.Y.Z rule real rather than a comment.
5. **#396 phase 1** and **#337**, which are the two the maintainer had already
   answered and neither has been started.

## What is worth knowing before touching anything

* **Publish the wiki, pull it and commit the pull *before* the tag.** The gate
  refused two releases in one day for shipping behind their own wiki, both
  times after twenty minutes of building. `CONTRIBUTING.md` says so now.
* **A release is 36 minutes** and about 30 seconds of it is compiling. Do not
  optimise the compile.
* **The labs are where the interesting failures are found.** #407's fix was
  wrong twice and both times a lab said so, not a unit test — and `lab/subif.sh`
  and `lab/routing.sh` had each been *asserting* the defective behaviour as
  ordinary, which is how it reached a maintainer's box with a green suite
  behind it.
* **`tools/lab.sh <name>` runs one lab**; the suite is `tools/run-all-labs.sh`
  and it takes about twenty minutes. A lab launched from a shell that returns
  is killed with it — use `setsid` to detach it.
