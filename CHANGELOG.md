# Changelog

## TL;DR

Every merge into `master` is a release: a version bump, an annotated tag and
an entry here. The project stays on `0.x.x` until the maintainer declares it
ready for production, so breaking changes are permitted and are called out
under their own heading. Newest release first.

The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
are to be interpreted as described in
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

**The list of issues at the head of a section is written when the section is
renamed to a version, not maintained by each merge.** It drifted three times in
one release cycle - seventeen branches each edited that one line, git resolved
some of those edits by keeping one side, and the list ended up naming a single
issue above seventeen entries. A list that every branch touches is a list that
every merge can lose. Written once, from the entries below it, it has nothing
to drift from.

**Renaming `## Unreleased` to a version MUST leave an empty `## Unreleased`
above it.** Without one, a branch cut before the rename has nowhere to put its
entry: git no longer finds that heading, anchors on the `### Fixed` of the

* **The RFC timeout floors are written down three times and nothing compared
  them.** The X-macro in `src/plugins/bpa/bpa_pool.h` is what the dataplane
  enforces; `docs/architecture.md` and the wiki page an operator reads carry
  the same four numbers. They agree today. An operator who shortens a timeout
  because a pool is filling reads the page, not the header, and a page carrying
  last year's floor would tell them a value is safe when the box will mark it.

  `test/test_doc_copies.py` compares them by name, seconds and RFC reference,
  and not by the prose after them: the three copies say what goes wrong in
  three different sentences, two of them in Portuguese, and demanding one
  wording would be demanding a translation nobody wants.

  The roadmap said "the three timeout floors" where there are four, which is
  the same drift one step further out, and is corrected.
section now at the top, and files the entry inside a release that has already
shipped - cleanly, with no conflict, and with nothing wrong in the branch. It
happened to five branches in one day after 0.68.0 was cut, one of them landing
in a section from February. `tools/release-gate.sh` caught every one, but it
catches them at the tag, which is where a release stops rather than where it is
cheap to fix. `test/test_changelog_landing.py` checks the heading is there.

A section that has already been tagged **MUST NOT** be edited at all: it
describes a medium somebody is running, and `tools/release-gate.sh` refuses a
release whose earlier sections are not what their tags shipped. A correction
goes in the CURRENT section, saying what it corrects, where a reader of the old
version has a chance of seeing it.

> The entries for **0.61.0, 0.61.1 and 0.61.2 were written after the fact**,
> reconstructed from the commits, because all three were tagged, built and
> installed with no entry here at all — leaving work an operator's box was
> already running described under *Unreleased*. That breaks this file's own
> rule ("at merge time, never afterwards from memory") and saying so is part of
> the repair: a reconstruction that hides being one is a second, quieter
> defect. `tools/release-gate.sh` now refuses a release whose version has no
> heading here, so the rule is enforced by the only thing in this project that
> has ever caught a release mistake rather than by whoever is doing the merge
> remembering it ([#195](https://github.com/andrediashexa/hexarouting/issues/195)).

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

### Fixed

* **The tuned `startup.conf` had never been read by a VPP, and 0.75.0 shipped
  one VPP refuses.**
  [#330](https://github.com/andrediashexa/hexarouting/issues/330) wrote a
  per-device `mtu` line for a card whose maximum is below VPP's default of
  9000. There is no `mtu` keyword in this VPP's `dpdk` configuration. An
  installed box answers

  ```
  vpp[3322]: dpdk_device_config: unknown input `mtu 1500'
  vpp.service: Start request repeated too quickly.
  ```

  and forwards nothing, with `Restart=always` turning the refusal into a loop
  ([#377](https://github.com/andrediashexa/hexarouting/issues/377)). Measured
  against the real binary in `hexarouting-dev:26.06`: inside a `dev` stanza
  `mtu`, `default-mtu`, `max-frame-size` and `max-rx-frame-size` are all
  `unknown input`, while `num-rx-queues`, `num-tx-queues`, `num-rx-desc`,
  `name`, `tag`, `tso` and `devargs` are accepted. There is no MTU knob in
  `startup.conf` at all; the MTU is a runtime interface property, which this
  product already sets over the binary API from the VTY's `mtu` statement.

  **The fix to the tuner is the maintainer's**; this entry is the reason
  nothing caught it. Every box this repository boots comes up in FIRST BOOT
  mode - the medium, the installed disk, and the release gate's boot of both -
  and that profile has no `dpdk` stanza at all. The tuned profile reached a
  real VPP only through `appliance/test_tuned_conf.sh`, which renders ONE shape
  and skips itself when the machine cannot spare hugepages.

  `appliance/test_startup_conf_shapes.sh` renders **nine** shapes with the real
  tuner and hands each to the real `vpp` binary: first boot, `no-pci`, one
  card, a card whose maximum MTU is below 9000, several workers and queues, a
  box with no worker, a tuner-sized `statseg`, an operator's own base carrying
  one, and two cards. The answer is VPP's, because the only authority on what
  VPP accepts is VPP: a list of legal keywords kept here would be a second copy
  of VPP's grammar written by the people who have just been shown inventing a
  keyword. It runs in `make test-appliance` and in `tools/release-gate.sh`,
  before the 325 MB image, and a run that cannot reserve hugepages reports
  `NOT MEASURED` - which the gate refuses, because VPP dies in *early* config
  without them and the parse error is never reached.

  Shown failing against the shipped product rather than against a mutation:
  on this tree the `lowmtu` shape is red and names the keyword. With the two
  lines that write it taken out of `appliance/hexarouting-tune`, all nine are
  green; restored, red again. Each of the other assertions was shown red on its
  own, nine of them, by mutating the fixture it reads.

## 0.75.0 - what the box said about the box it was on, and the checks that could not fail

[#248](https://github.com/andrediashexa/hexarouting/issues/248), [#277](https://github.com/andrediashexa/hexarouting/issues/277), [#288](https://github.com/andrediashexa/hexarouting/issues/288), [#295](https://github.com/andrediashexa/hexarouting/issues/295), [#312](https://github.com/andrediashexa/hexarouting/issues/312), [#317](https://github.com/andrediashexa/hexarouting/issues/317), [#326](https://github.com/andrediashexa/hexarouting/issues/326), [#327](https://github.com/andrediashexa/hexarouting/issues/327), [#328](https://github.com/andrediashexa/hexarouting/issues/328), [#329](https://github.com/andrediashexa/hexarouting/issues/329), [#330](https://github.com/andrediashexa/hexarouting/issues/330), [#331](https://github.com/andrediashexa/hexarouting/issues/331), [#332](https://github.com/andrediashexa/hexarouting/issues/332), [#333](https://github.com/andrediashexa/hexarouting/issues/333), [#334](https://github.com/andrediashexa/hexarouting/issues/334), [#335](https://github.com/andrediashexa/hexarouting/issues/335), [#336](https://github.com/andrediashexa/hexarouting/issues/336), [#338](https://github.com/andrediashexa/hexarouting/issues/338), [#339](https://github.com/andrediashexa/hexarouting/issues/339), [#342](https://github.com/andrediashexa/hexarouting/issues/342), [#343](https://github.com/andrediashexa/hexarouting/issues/343), [#344](https://github.com/andrediashexa/hexarouting/issues/344), [#346](https://github.com/andrediashexa/hexarouting/issues/346), [#347](https://github.com/andrediashexa/hexarouting/issues/347), [#348](https://github.com/andrediashexa/hexarouting/issues/348), [#349](https://github.com/andrediashexa/hexarouting/issues/349), [#350](https://github.com/andrediashexa/hexarouting/issues/350), [#351](https://github.com/andrediashexa/hexarouting/issues/351), [#352](https://github.com/andrediashexa/hexarouting/issues/352), [#353](https://github.com/andrediashexa/hexarouting/issues/353), [#354](https://github.com/andrediashexa/hexarouting/issues/354), [#355](https://github.com/andrediashexa/hexarouting/issues/355), [#356](https://github.com/andrediashexa/hexarouting/issues/356), [#357](https://github.com/andrediashexa/hexarouting/issues/357), [#358](https://github.com/andrediashexa/hexarouting/issues/358), [#359](https://github.com/andrediashexa/hexarouting/issues/359), [#360](https://github.com/andrediashexa/hexarouting/issues/360), [#361](https://github.com/andrediashexa/hexarouting/issues/361), [#362](https://github.com/andrediashexa/hexarouting/issues/362), [#363](https://github.com/andrediashexa/hexarouting/issues/363), [#364](https://github.com/andrediashexa/hexarouting/issues/364), [#365](https://github.com/andrediashexa/hexarouting/issues/365), [#367](https://github.com/andrediashexa/hexarouting/issues/367), [#368](https://github.com/andrediashexa/hexarouting/issues/368), [#369](https://github.com/andrediashexa/hexarouting/issues/369), [#370](https://github.com/andrediashexa/hexarouting/issues/370), [#371](https://github.com/andrediashexa/hexarouting/issues/371), [#372](https://github.com/andrediashexa/hexarouting/issues/372), [#373](https://github.com/andrediashexa/hexarouting/issues/373), [#374](https://github.com/andrediashexa/hexarouting/issues/374).

### Changed

* **The rebranding to Prisma Routing is on the roadmap as work, not as a
  question.** The maintainer has decided the name and scheduled it, so
  [#202](https://github.com/andrediashexa/hexarouting/issues/202) moves out of
  `docs/roadmap.md`'s "what is blocked on somebody else" table and becomes
  **Phase 8** of the delivery plan.

  **The name is two words.** The file and the issue both carried it as
  `PrismaRouting` for four days, which is a different name. Where it has to be
  one token - a package, a unit, a path, a group - it is `prismarouting`, and
  that is a spelling of the two-word name rather than a second name. A product
  whose own roadmap cannot spell it is one whose banner, whose ISO filename and
  whose release note will not agree either.

  The size was re-taken rather than quoted, because the old figures were four
  days old and the trend is the argument. On 2026-09-11: 905 lines across 101
  files carrying `hexarouting`. On 2026-09-15: **2,809 across 166**, with
  **1,033 across 117** carrying `hexash`, **622 across 61** carrying a `HEXA_`
  variable, **105 across 17** carrying a role group name, and 13 carrying
  `Hexanetworks`, which is the company and stays. It tripled in four days
  because every release writes the name into this file and every sweep writes
  it into a document, so the cost of this rename is a function of how long it
  is deferred rather than of anything being built.

  Five decisions were open on the issue and four now carry a recommendation
  with its reason: `hexash` keeps its name, because it is the recorded login
  shell of accounts that already exist and an account whose shell does not
  exist cannot log in; the role groups rename by `groupmod -n` and never by
  recreation, because membership is by GID and a recreated group silently takes
  every operator's role away; `/var/log/hexarouting` does not move, because it
  is a separate filesystem holding a record under a one-year retention
  obligation and moving a mountpoint under that is a data migration; and the
  remote renames with the old URLs still resolving, because 74 release notes
  and this file point at it. The fifth - whether boxes in service upgrade at
  all - is the maintainer's, is marked as such, and everything else follows
  from it.

  Two ordering rules are unchanged and are why it is Phase 8 rather than Phase
  1: it MUST ship alone, and it SHOULD wait until the worker-path defects are
  closed, because a rename touching 2,809 lines makes every later `git blame`
  on those files answer with the rename.

### Fixed

* **The release gate refused a medium that had booted and said it was ready.**
  Found by cutting this release: the gate built the ISO, booted it, and stopped
  with *"the medium booted and its self-test reached no verdict"*. The box had
  reached one. Line 704 of its console reads
  `HexaRouting ready, with 3 warning(s) above.`

  The gate was not asking the box. It greps `boot.log`, which is what
  `lab/boot-iso.sh` PRINTED, and what that lab prints of the self-test is
  `sed -n '/boot self-test/,/=====$/p' /tmp/clean.log | head -40`. **The report
  reached 46 lines in this release** and the verdict is its last line, so the
  cap swallowed it. Measured on the run that refused: the lab's rendering is 49
  lines and carries no verdict; the box's own console carries it.

  That is the shape [#369](https://github.com/andrediashexa/hexarouting/issues/369)
  is about, in the gate instead of in a reader - *the corpus was the lab's
  rendering of the box's output and not the box's output*. `lab/boot-iso.sh`
  asks `/tmp/clean.log` for the same verdict and was right all along; only this
  caller was reading second-hand. All four of the gate's boot readers now ask
  the console copy, and its **absence is a refusal** rather than a skip: a gate
  that shrugs when the console is missing is a gate that publishes a medium
  nobody watched boot.

  The lab's own cap is raised to 120 in the same breath, because whoever runs
  that lab by hand was not being shown the verdict either. It stays a cap and
  not a length: the `sed` range is bounded by the report's own closing rule, so
  `head` is only there for a console that lost it.

* **Two checks that went red on work that was correct, both because the product
  got better.** `test/test_release_gate.sh` hard-coded that **two** of the
  gate's document checks refuse its fixture, on the stated argument that the
  wiki check *"is red here whatever else happens"*. That stopped being true the
  day the wiki was published. The case then reported that a working gate had
  refused and never said so, when it had said so about a different number.

  It is the same disease this release cured in `test/test_wiki_roundtrip.sh`,
  caught in a second file. Both cases now **read the count off the run** - each
  document refusal opens with `error: ` at column zero - and demand the summary
  say that number, so they go red if the gate miscounts in either direction and
  do not care which checks the fixture happens to break. A guard beside them
  refuses a run where nothing refused at all, so the assertion cannot pass by
  measuring a clean run.

* **A needle that matched a label rather than a call.**
  `test/test_iso_ready.sh` finds the Makefile targets that open the medium by
  finding the scripts under `tools/` that **mention** one of the labs which
  open it. The gate's new console-log refusal says *"lab/boot-iso.sh copies the
  guest's console there"*, which is a sentence for whoever reads the refusal
  and not a call - and that made `release-gate` a target that opens the medium,
  so the check demanded it consult `tools/iso-ready.sh`, a guard about a
  PREBUILT image the gate does not use because it builds its own. Comment lines
  and message lines are dropped before looking now. Shown still failing on the
  real defect: with the `iso-ready.sh` line taken out of the `test-iso` recipe,
  it names it.

* **Two defects the stride work uncovered, recorded rather than fixed**
  ([#375](https://github.com/andrediashexa/hexarouting/issues/375),
  [#376](https://github.com/andrediashexa/hexarouting/issues/376)). Both were
  measured while driving the VTY for
  [#373](https://github.com/andrediashexa/hexarouting/issues/373), and both are
  the shape this project puts above everything else: something the product
  offers and does not do.

  `block-stride` is accepted on a **deterministic** pool and never applied -
  the only read of it is guarded on the dynamic allocator - while
  `show bpa pool` prints the layout promise and the counters that would show it
  doing nothing print only for dynamic pools. The product already refuses the
  mirror image and says why beside it.

  A `hexash -c` batch whose statement is refused **keeps going and commits what
  it could**, printing `commit complete`. `Shell.load()` answers the same
  question the other way for a saved configuration file, and its comment is the
  argument.

  Neither is fixed here. They are in `docs/roadmap.md` with the measurements,
  because `tools/roadmap-state.sh` refuses an open issue the file never
  mentions - correctly: the roadmap is what somebody reads to decide what to do
  next, and an issue it never names is work hidden from that reader.

* **Two defects that existed only once the branches were together, and one of
  them was a reader that died instead of going red.** Neither was in any
  branch: both appeared when six of them met in `master`, which is the case no
  branch can test for itself.

  **A reader with no verdict.** The new `SAY_UNSEEN_AT_MOST` ceiling read its
  count with `awk ... "$SAY_DIR/unseen-printers" 2>/dev/null`, and that file is
  written only when there IS a printer the boundary rule missed. awk exits 2 on
  a file that is not there; `2>/dev/null` hides the message and not the status;
  and a caller with `set -e` dies inside the assignment. `test_plugin_says.sh`
  does `set -eu`, so it stopped mid-run having printed no verdict at all.

  It was reported as a suite that **crashed** rather than one that failed, by
  the counter [#338](https://github.com/andrediashexa/hexarouting/issues/338)
  added for exactly this: *"a suite with NO VERDICT never printed one of the
  three lines it ends with, so it did not fail a check - it stopped before
  reaching one"*. That distinction is the only reason this was recognised in
  seconds rather than hunted. A reader that dies is worse than a reader that is
  red, because the red says something about the product.

  **A skip counted as a refusal.** `tools/release-gate.sh` ran the roadmap
  holder as `if ! rm_out=$(...)`, which reads **any** non-zero as disagreement.
  Both holders exit 1 for findings and 2 for *nothing was compared*, and say so
  in their own headers, because a document nobody compared reads exactly like
  one that agreed. So a tracker that answered with an empty list - an expired
  token, a renamed repository, a rate limit behind a wrapper - **refused the
  release**, which is the outcome the gate's own comment argues against three
  lines from where it is argued.

  It had been latent since the roadmap check was wired in and became visible
  the moment a second holder was added beside it. Both now branch on the exit
  status, and `test/test_release_gate.sh` drives it: a stub answering nothing
  makes both checks say the comparison did not happen, and the count stays at
  the one refusal the fixture's own state produces. Shown red by making the
  gate stop recognising exit 2.

  The summary line said *"of the three document checks"* where there are now
  four, which the suite caught by needle. It names all four.

* **The coverage reader reported full coverage of screens it never looked
  for.** `say_messages` in `test/say-coverage.sh` finds a printer at the start
  of a line or after one of `( ) ; | & {`. A shell has other places to put
  one, and the commonest is after a word:

      for f in $FILES; do echo "# yours: $f"; done

  `do`, `then` and `else` are words, not punctuation, so neither arm matched
  and the message was not looked for at all - it fell to the reader's `else
  next`, which is the arm for a line that is not a printer call. The reader
  then went on to report every message of that program accounted for.

  Measured before deciding what it was: **four such sites across the five
  programs these readers cover, and not one of them hides a screen today.**
  Two are inside `ask()` at `appliance/hexarouting-install:180-181`, which
  `say_row_enders` finds anyway by scanning the function body for a `printf`
  regardless of this rule; one is a printer definition, which this pass
  excludes deliberately; and one is `install.sh:579`, inside a brace group
  redirected into a file, which is not a console row either. So it is a limit
  of the rule and not a screen going unmeasured, and widening the rule would
  buy nothing and cost the false positives #369 was about.

  What was wrong was the silence. The sites are now recorded and held to
  `SAY_UNSEEN_AT_MOST`, printed with file and line like every other skip list
  here, so the day a fifth appears somewhere that does print, somebody sees it
  instead of the reader reporting complete coverage of a program it never read
  properly. `test_install_says.sh` sets the ceiling to 1 and
  `test_installer.sh` to 2, each with the reason written next to the number.

* **A refusal that gave advice about the wrong thing.** `say_ceiling` is used
  by every ceiling in `test/say-coverage.sh`, and it wrote the width sentence
  into all of them: *"a row that does not fit is broken mid-word by a console
  that cannot reflow ... move the value off the row"*. True of the two width
  ceilings and false of the rest. A check that fails and then sends the reader
  to shorten a row that has nothing to do with the failure costs more than a
  refusal with no advice at all. The advice is now the caller's, defaulting to
  the width sentence because two of the three callers want it.
* **The roadmap still presented the published wiki as work blocked on the
  maintainer** ([#277](https://github.com/andrediashexa/hexarouting/issues/277)).
  The wiki went out this cycle - 32 pages, one of them created because the wiki
  did not have it at all - and `docs/roadmap.md` went on listing it under *what
  is blocked on somebody else*, saying `what is left is the token`. The token
  arrived and the pages are live.

  This is the shape `tools/roadmap-state.sh` exists to catch, and it caught it:
  *"closed issues the file still presents as work that remains"*, naming #277
  and the line. It is also a release blocker, because `tools/release-gate.sh`
  runs that holder, so the correction is not tidiness - the next release cannot
  be cut past it.

  The row is gone and the delivery is recorded in the past tense, including the
  part that was not obvious: the `wiki/.pulled` interlock was **checked and not
  assumed**. Every page's `updatedAt` was read before anything was written, the
  one human edit found that way was fetched and proved carried forward, and only
  then was `.pulled` advanced. The 32 were verified afterwards by a needle from
  their own content rather than by a digest, because the wiki normalises
  markdown on the way in and a checksum would have answered about the
  normalisation instead of about the page.
* **Three completed issues the CHANGELOG named nowhere, and nothing had ever
  asked.** CONTRIBUTING.md section 5 rule 3 makes an entry part of every merge,
  and the file is what an operator who does not clone this repository reads to
  find out what changed for them. Measured over the whole tracker: **293 closed
  issues, 291 of them completed, and three that this file does not mention at
  all.** Each of the three below was found by hand while writing a release note,
  one at a time, which is why they were found in the first place and why nothing
  would have found a fourth.

  They are entered **here** and not in the sections that shipped them, because
  this file's own TL;DR forbids editing a shipped section: a reconstruction that
  hides being one is a second and quieter defect, and a reader who has the old
  version has no way of seeing a change made behind it.

  **[#175](https://github.com/andrediashexa/hexarouting/issues/175) - `router
  isis <TAB>` completed `<word>` and nothing after it, shipped in v0.62.0.**
  The instance tag was a bare hint, so an operator writing `ip router isis` on
  an interface had to remember what they had called the instance, and getting it
  wrong produces an interface that joins nothing, silently. It completes from
  the box now, and the block offers the statements an ISP core uses, including
  `metric-style wide` - narrow metrics cap at 63 and cannot express a 10G
  topology, which is the classic silent limitation.

  **[#177](https://github.com/andrediashexa/hexarouting/issues/177) -
  `lab/igp.sh` asserted on files the lab had written, shipped in v0.61.0.** The
  lab wrote this box's own `ospfd.conf` and `isisd.conf`, so every OSPF and
  IS-IS assertion under them was evidence about FRR reading a file the lab
  wrote rather than about the VTY. That is the shape this project refuses by
  name: a lab that performs the step the product is missing proves nothing. Both
  files are a hostname line and nothing else now, the way `bgpd.conf` was
  emptied for [#142](https://github.com/andrediashexa/hexarouting/issues/142),
  and neither the OSI punt nor the interface MTU is set at setup.

  **[#341](https://github.com/andrediashexa/hexarouting/issues/341) -
  `wiki-pull.sh`'s empty-answer guard could not fire.** It was
  `[ -s "$TMP/docs.json" ]`, and an answer holding no published documents reads
  out as `[]`, which is two bytes and passes a size test. Measured against a
  stub: the rewrite ran, **every page in the mirror was deleted**, `wiki/.ids`
  came back empty, `wiki/.pulled` was written empty and the script exited 0
  saying `0 pages`. That script's whole first half exists to stop `wiki/` being
  emptied. The guard is the document count now, which is the number it was
  always about. This one was still unreleased, so it is in its own release's
  section and not a repair.
* **The release-title check passed three ways without comparing anything**
  ([#340](https://github.com/andrediashexa/hexarouting/issues/340)). The check
  that issue added does compare, and that was measured first: with the
  `## 0.74.2` heading extended by a second clause and the tag moved onto it in
  a throwaway clone, the gate refuses and quotes both halves. Three other
  inputs reached its green line having compared nothing, each driven rather
  than argued:

  | input | what it said | what it says now |
  |---|---|---|
  | `gh` missing, or not authenticated | nothing at all | `the release title was NOT checked: gh cannot answer` |
  | `gh` authenticated, the API failing | `v0.74.2 has no published release yet` | `the release title was NOT checked: gh could not read v0.74.2`, with gh's own error under it |
  | a heading carrying the version and no sentence | `the published release title is the CHANGELOG heading` | refused, saying the heading has no sentence to compare against |

  The first two are one cause. The call ended in `|| true`, so a tag with no
  release, an API that could not answer and a gh that is not installed all
  arrived as the same empty string. The middle row is the one that matters:
  it is not silence but a false statement, made at the moment somebody is
  deciding whether to publish, about a release that has existed since
  2026-09-15T01:30:53Z. The third row is the guard `[ -n "$head_title" ]`,
  and it is the same defect the `## VER` section extraction refuses in words
  150 lines above it in the same file - an extraction that found nothing has
  quietly stopped checking.

  **Not a defect, and stated so it is not rediscovered:** the check runs
  BEFORE `gh release create`, so the release being cut has no title yet and
  the gate says so and passes. Measured by cutting a v0.74.3 in a throwaway
  clone. It catches drift on a later run over that tag and it cannot gate the
  publication itself; what it can do instead is hand over the repair, so the
  refusal now ends with `gh release edit <tag> --title "<version> - <heading>"`
  filled in, which is what was typed by hand four times.

  `test/test_release_gate.sh` gains five assertions and drives the comparison
  for the first time. Its own note said no fixture can publish a release for
  its own tag, which is true of GitHub and irrelevant: the suite already
  replaces `gh`, so the stub can answer what a published release would. Four
  of the five are red against the gate as it was, and the stub now prints
  `release not found` on stderr the way real `gh` does, because that line is
  what tells a missing release apart from an API that could not answer.
* **Three of `README.md`'s ten state-table rows had nothing holding them, and
  one of the three was false**
  ([#374](https://github.com/andrediashexa/hexarouting/issues/374)). That table is what a reader consults to decide
  whether to trust this product, and two of its rows were read and corrected
  earlier in this cycle - the appliance row, which claimed a release gate that
  26 of the 74 published releases predate, and the configuration trail row,
  which claimed every entry names the operator where the product has a shape
  that says it cannot. Both were found by reading. The question nobody had
  asked was what held the other eight, so each was measured against the tree:
  what in `test/`, `tools/` or `lab/` goes red if this row becomes false?

  **The Throughput row was false, and had been for 66 releases.** It said
  *"NOT measured - every performance figure is untested"*. The first half is
  true of bits per second and [#28](https://github.com/andrediashexa/hexarouting/issues/28)
  stays open for it, because that needs a NIC DPDK drives natively. The second
  half is not: `lab/throughput.sh` has measured what a packet costs the
  translation nodes in CPU clocks since v0.8.0, `tools/run-all-labs.sh` runs it
  in the release suite, and `docs/roadmap.md` section 3.5 states the number it
  produced - **346 clocks** in `bpa-in2out`. A reader deciding whether this
  design can reach a target at all was being told the one measurement that
  answers them does not exist. The row now separates the figure nobody has
  taken from the one this project takes on every suite run.

  **Six rows are held by a lab and by nothing else, and that is left as it
  is.** "Routes reach the VPP FIB" is `lab/igp.sh` and `lab/routing.sh`
  requiring a prefix out of `vppctl show ip fib`; "verified with a decoding
  collector" is `lab/ipfix.sh` and `lab/ipfix-tcp.sh` reading
  `tools/ipfix_collector.py`'s decoded records; the roles half of the VTY row
  is `lab/vty.sh` driving three real POSIX accounts. `make test` runs offline
  and can answer none of them, and a check written here that looked as though
  it did would be worse than the nothing it replaced.

  **One clause has no holder in any suite at all**, and saying so is the point
  of measuring: the dataplane row's *"and real traffic"* is `lab/hw-vpp.sh`,
  which needs two machines and a NIC bound to `vfio-pci`, and which
  `tools/run-all-labs.sh` excludes for that reason. `lab/hw-lab.sh` is 77 lines
  of setup and asserts nothing.

  **Four rows are answerable from the tree, and now are.** Each is in
  `test/test_doc_copies.py`, beside the two from this cycle, with one owner of
  the truth and one reader of it:

  | Row | Owner of the truth | What makes it red |
  |---|---|---|
  | BPA mapping core, *"every mapping in the pool one by one"* | `ref/gen_vectors.py`, through the counts in the header it generates | a generator that samples. `test/test_bpa_pool.c` walks whatever the vectors hold and never asks how many there should be - at `BPA_VEC_N_FWD` zero it prints "0 comparisons" and passes. The check recomputes the total from each pool's own capacity and inside count: 5031 forward mappings across 9 pools, and 2517 when the enumeration is cut to every second subscriber |
  | Connection records, *"on their own filesystem"* | the fstab `appliance/hexarouting-install` writes | one UUID variable for both mount points, or no line for `/var/log/hexarouting` at all. `appliance/selftest` reports the arrangement on a running box and `test/test_selftest.sh` drives both of its arms, so what an installed box does was held; what the installer writes was not |
  | HA / failover, *"NOT implemented"* | `tools/run-all-labs.sh`'s `NOT_LABS`, which excludes `lab/ha-failover.sh` because the feature is MISSING | the day [#9](https://github.com/andrediashexa/hexarouting/issues/9) lands that lab joins the suite and this row would still say the feature is absent, which is how a README goes stale here: by describing a smaller product than the one that now exists |
  | Throughput, *"every performance figure is untested"* | `lab/throughput.sh`, while the release suite runs it | the clause above, put back |

  Every one of the four was shown red in both directions before it was kept -
  the owner broken with the document left alone, and the document broken with
  the owner left alone. `TESTING.md`'s fourth way a mutation lies is the trap
  for a documentation check specifically: mutating the sentence the needle is
  cut from moves both sides together and proves nothing.

  Also corrected: `lab/records-full.sh`'s header quoted the configuration trail
  row as it read before this cycle, which made it a second copy of a state
  table row that had already moved.
* **The block-stride warning was on the `vppctl` path only, and on the path an
  operator takes to create a pool it did not even reach the log**
  ([#373](https://github.com/andrediashexa/hexarouting/issues/373)).
  `bpa_stride_warn()` had two callers: the dataplane's own console handler, and
  `bpa_pool_settings_set()` with a null `vlib_main_t`, which only writes the
  record. `bpa_pool_add()` called neither. An operator who configured a
  `block-stride` from the VTY - which is every operator, since the VTY exists
  so nobody has to drop to the dataplane console - was told nothing at the
  prompt,
  and on creation nothing anywhere: `block-stride` rides on the creation
  message, so a new pool never goes through `settings_set` at all. Meanwhile
  the pool configuration page on the public wiki told them the box warns at
  pool creation and quoted the three rows it prints.

  **What they lost.** The stride is what keeps a subscriber's extra blocks near
  its first one, so an investigator reading a log sees one contiguous holding
  instead of two unrelated ranges. A stride the worker sharding cannot honour
  is not honoured for anybody - every block it names belongs to another
  worker - and extensions fall back to the free list, silently: the subscriber
  still
  gets a block and still translates, so from outside a stride that never
  applies is indistinguishable from one that does. The warning names the
  nearest stride that WOULD work, which is the difference between a five-second
  correction at the prompt and finding out months later from a log that does
  not read the way the operator expects.

  **Both ways in, because a stride reaches the dataplane by two messages and
  neither covers the other.** A pool created with a stride carries it on
  `bpa_pool_add_del`, so the warning is now said in `bpa_pool_add()`, after
  `bpa_pool_init()` because the arithmetic reads values it computes. A stride
  changed on a pool that is already carrying traffic is an in-place setting -
  the stride decides where the NEXT extension goes and moves nothing already
  handed out - and that call warned to the log already.

  **And hexash relays it, the way it relays a timeout under its floor.** The
  device owns the sentence, the arithmetic and the worker count; `show bpa
  pool` says it under the pool it is about, and a commit that sent a stride to
  a pool reads that back and prints it at the prompt. Only for those pools: the
  device answers about the whole box, and a warning that fires whatever the
  operator did is one they learn to scroll past. The show does not write a log
  record - a screen that logged every time somebody looked at it would fill the
  log with one event repeated by its own reader - so `bpa_stride_warn()` was
  split into a guard that owns the arithmetic, a `say` that owns the rows and a
  `warn` that is the say plus the record, exactly as
  [#372](https://github.com/andrediashexa/hexarouting/issues/372) split the
  reserve.

  **`show bpa pool` is the right screen for it, and not merely the convenient
  one.** That screen already prints `stride 4 (2048 ports between a
  subscriber's blocks, on its own public address)` - a promise about layout -
  and whether that promise holds is not a property of the pool at all: it
  depends on the thread count the box happened to start with, which the pool
  does not carry and the formatter cannot read. So the warning is not a second
  unrelated sentence on that screen; it is the correction of a row the same
  screen printed a few lines above it, and leaving it off means the screen
  states a layout the box is not producing. `show bpa blocks` was the
  alternative and was rejected: its `stride N: X placed, Y fell back` counters
  are the honest reading AFTER traffic, but at the moment of a commit they are
  two zeros, they are printed only for a dynamic pool, and on a box whose
  pools are all deterministic that screen does not exist - a readback that
  reports nothing on a correct box and nothing on a broken one is not a
  readback.

  `lab/dynamic-workers.sh` carries the case, beside the two `vppctl` ones it
  already had: same box, same two workers, same run, and the only difference
  is which way in was used. It asserts the numbers rather than the appearance -
  the ways the blocks are sharded, what one stride step moves by, the nearest
  honourable stride - and guards on the pool having been created and the box
  having workers, because otherwise "the warning did not appear" is true for
  the wrong reason. It drives the in-place path too, and carries the control a
  relay needs: a second pool with a stride this box CAN honour, committed while
  the first pool is still on the box saying it cannot, so a relay that
  forwarded everything it read would be caught.

* **A check that compared two trees, and the day the wiki was published is
  the day it broke.** `test/test_wiki_roundtrip.sh` drives the release gate and
  requires it to agree with `tools/wiki-status.sh` rather than asserting a
  verdict - and its comment says exactly why: *"'The gate refuses' would be a
  check that breaks the day the wiki is published, which is the day the product
  gets better"*.

  It got that right and then asked the two of them about **different trees**.
  The holder was run against the working tree; the gate clones the repository
  and checks out a tag. Those agreed only for as long as nothing had been
  published since the newest tag. When the 28 pages went out, the working tree
  went clean, the tag stayed behind, and the check reported *"the gate is
  deciding this for itself"* about a gate that was reading the holder
  correctly.

  One question, one tree: the holder is asked about the gate's own clone now,
  and so is the page list in the check beside it, which had the same fault for
  the same reason. Shown failing with the gate made to decide for itself, which
  is what the check is for and what it still catches.

* **44 of `appliance/install.sh`'s 75 messages were printed by no run, and the
  measurement that said otherwise was reading the wrong thing**
  ([#371](https://github.com/andrediashexa/hexarouting/issues/371)).
  [#364](https://github.com/andrediashexa/hexarouting/issues/364) gave the
  installer, the NIC binder and the tuner a message-coverage reader over
  `test/say-coverage.sh`. `appliance/install.sh` did not get one, and it is the
  program that puts the files on the box - on a first install and on every
  upgrade, which is the path
  [#295](https://github.com/andrediashexa/hexarouting/issues/295) opened.

  **The first measurement said 71 of 75 covered and was measuring suite
  stdout.** The five suites that run this program redirect everything it prints
  into a file - `sh "$TREE/appliance/install.sh" > "$2" 2>&1` - so none of it
  reaches the suite's own stdout and a corpus built from there sees almost none
  of it. That design is right and is not changed: `test/test_install_says.sh`
  keeps stdout and stderr in two files on purpose, because
  [#301](https://github.com/andrediashexa/hexarouting/issues/301) is a defect
  about which stream a sentence went to, and a tee that merged them would buy
  the corpus by taking away the question the suite exists to ask.

  **So `SAY_CORPUS` grew a third caller rather than a third name.** `say_capture`
  in `test/say-coverage.sh` is handed the file a suite already wrote, after the
  run: it boxes it when that suite has a reader open, and keeps it under
  `$SAY_CORPUS/<program>.runs/<suite>.<run>.out` when somebody named a
  directory. Off unless somebody does, exactly as `SAY_CORPUS` and
  `tools/run-all-labs.sh`'s `LAB_OUTPUT`
  ([#366](https://github.com/andrediashexa/hexarouting/issues/366)) are off, and
  for the same reason: a suite that writes outside its own tempdir on every run
  leaves litter in somebody's repository. `test/test_manifest.sh` sources the
  file for that one function and no reader.

  **The reader is at the bottom of `test/test_install_says.sh`**, over the boxes
  that were already there, and it accounts for all 68 messages this program can
  print to a console, over 11 boxes. Two boxes are new and are what the count
  needed: the `HEXA_VTY_LOGIN=1` arm that every shipped medium is built by and
  that no run in this repository had ever taken, and the service-enabling branch
  behind `[ -z "$DESTDIR" ]`, driven at its own seam with a `/run/systemd/system`
  in a mount namespace rather than this machine's - read off the machine, that
  answer is green on a laptop and red in a build container over a product nobody
  changed. Both halves, no allowlist. The widths half comes with the reader:
  0 rows over the 79-column console, and the one row carrying a value with no
  widest is `installed into <DESTDIR> (services enabled offline)`.

  **What the reader does NOT count, said out loud.** The 75 in the issue
  includes seven rows of the MANIFEST header, which `say_messages` drops because
  they are a brace group redirected to a file - the same rule that drops
  `write_network_config`'s management stanza in `appliance/hexarouting-install`,
  and for the same reason: that file is read back by name, by
  `test/test_manifest.sh`, in a section of its own. They are a file's contents
  and not a screen, and measuring them against a console would measure a
  64-character checksum against 79 columns.
* **The reserve warning was on the `vppctl` path only, so no operator using
  the VTY was ever told**
  ([#372](https://github.com/andrediashexa/hexarouting/issues/372)).
  `bpa_reserve_warn()` had one caller, the dataplane's own console handler.
  hexash applies pools over `bpa_pool_add_del`, and that path called neither
  it nor anything that would. An operator who configured a `reserve` from the
  VTY - which is every operator, since the VTY exists so nobody has to drop to
  the dataplane console - saw the warning neither at the prompt nor in the
  log, while the wiki page they read told them the box warns at pool creation.

  **What they lost.** The reserve is sharded by index like every other block,
  so a reserve of one block on a box with two workers leaves one worker with
  nothing to give: the subscribers that hash to it fill their block and are
  refused a second one every time. From outside that is indistinguishable
  from a reserve that is merely busy - the customer stays full while
  `show cgnat pool` reports a reserve - and nothing said so at the one moment
  the operator could have fixed it by raising the number or adding an address.

  **One call site covers creation and every later change, and that was
  established rather than assumed.** `reserve` is deliberately absent from
  `bpa_pool_settings_set`, whose contract is "safe on a pool carrying
  traffic": a reserve comes out of capacity, capacity is the divisor under the
  blocked policy, so changing it re-maps subscribers. hexash classifies it
  outside `in_place` for the same reason, which puts a change to it in
  `pool_replace` - remove and add - rather than in `settings_set`.
  `lab/commit.sh` already drives that: lowering a reserve at the VTY is
  refused as a re-mapping change and needs `commit remap`. So creation and
  every later change arrive at `bpa_pool_add()`, and that is where the warning
  now is, with a null `vlib_main_t` exactly as `bpa_pool_settings_set()` warns
  about timeouts and strides.

  **And hexash relays it, the way it relays a timeout under its floor.** The
  device is the only owner of the sentence, the arithmetic and the worker
  count; `show bpa pool` says it under the pool it is about, and a commit that
  created or recreated a pool with a reserve reads that back and prints it.
  Only for the pools the commit touched: the device answers about the whole
  box, and a warning that fires whatever the operator did is one they learn to
  scroll past. The show does not write a log record - a screen that logged
  every time somebody looked at it would fill the log with one event repeated
  by its own reader - so the record for a pool that is created is written
  once, by `bpa_pool_add()`.

  `lab/detreserve.sh` carries the case, beside the `vppctl` one #370 added:
  same box, same workers, same run, and the only difference is which way in
  was used. It asserts the numbers rather than the appearance - the workers
  holding no reserve, the reserve to raise to, what one more reserve block per
  address costs - and guards on the pool having been created and the box
  having workers, because otherwise "the warning did not appear" is true for
  the wrong reason. It found one on the way: the first arrangement was refused
  for a block size the VTY does not accept, and the guard reported a pool that
  was never created instead of a missing warning.

* **The plugin message reader named a branch unseen that two labs were
  printing, so the 38 and the 48 it published were an upper bound**
  ([#369](https://github.com/andrediashexa/hexarouting/issues/369)).
  `tools/plugin-says.sh` found a real defect the day it landed - `bpa_audit.c`
  had a plural tamper branch nothing had ever run, and `lab/audit.sh` produces
  it now. It went on naming it after the corpus was refreshed with the phrase
  provably in it, and a branch that ran and is reported as never run is the
  failure that makes a list people learn to ignore.

  **It was the most-specific-message walk, not the indentation.** The reader
  spends a row on the message with the most literal characters, takes that
  message out and reads what is left. What it took out was the message's
  **span** - first literal fragment to last - and a format string's span
  swallows every value interpolated between its fragments:

  ```c
  vlib_cli_output (vm, "    the sequence jumps from %llu to %llu: %llu "
                       "entr%s removed from the middle", ...,
                   this_seq - expect == 1 ? "y was" : "ies were");
  ```

  `ies were` is eight characters, exactly at `SAY_MIN`, and it reaches the
  screen in the middle of the row its own format printed. The format won the
  row and the branch went out with it. It now strikes out the winner's
  **fragments** and leaves the values alone: a message owns the words it
  wrote, not what it was handed.

  **The lab indentation was a coincidence and is measured as one.**
  `printf '%s\n' "$X" | sed 's/^/  /'` is the house idiom for showing a screen
  inside a lab, and the reproduction changed behaviour at exactly the column
  where the format's own four spaces stop fitting - which looked like the
  cause. It is not: the matcher finds every fragment with `index()` and
  anchors nowhere, so columns added in front of a row can never remove a
  match. Indenting **every line of all 89 lab transcripts** by two more
  columns leaves the unseen list byte for byte identical; stripping the
  leading whitespace instead takes it from 38 to 64, because the four spaces
  are part of the message. Nothing was changed in the labs, and nothing in the
  matcher ignores whitespace.

  **It cannot attribute a row the box did not print, and it is stricter than
  what it replaces.** Striking out leaves the row the length it was, so every
  position the walk reports is a position on the row as printed and every
  fragment found after a strike is found in text the box really wrote there.
  The cut it replaces glued the text in front of a match to the text behind
  it, and a needle could match across that seam - a sentence assembled from
  two pieces the console never showed side by side. `test/test_plugin_says.sh`
  drives both: a corpus at four indentations where the branch must be found,
  and two where it must not - one row with a word altered in the middle of it,
  one carrying the words that only the old seam could have joined. The second
  goes green on the old code, which is the false attribution it was making.

  **The re-measured worklist is 37 over `bpa_cli.c` and 46 over the plugin**,
  against 38 and 48 published on
  [#366](https://github.com/andrediashexa/hexarouting/issues/366) and
  [#368](https://github.com/andrediashexa/hexarouting/issues/368). Two messages
  moved and both were really printed: `bpa_audit.c:560,647  ies were`, on lines
  111 and 116 of `audit.out`, and `bpa_cli.c:273  reciprocal`, the divisor
  `show bpa pool` prints on four transcripts. Everything else on that list is
  still a screen no box in this repository has ever reached.

* **A ceiling printed what it was holding only when it broke.**
  `say_ceiling` in `test/say-coverage.sh` named its rows under `bad` and only
  the count under `ok`, so the installer's 49 unsafe rows and the binder's 6
  could be read only by lowering the variable to provoke a red run - and
  nobody lowers a variable whose contents they cannot see. They are printed on
  the green run now, with the line, the columns before the value and what
  bounds each slot, which is what the `NOT MEASURED HERE` block beside them
  and the unseen list in `tools/plugin-says.sh` already do: a check says out
  loud what it deliberately does not hold, where the next person is standing.
  A ceiling of zero still prints nothing under it, and
  `test/test_plugin_says.sh` holds both - it drives the width reader with a
  stub program of its own and requires a non-zero ceiling to name its rows.
  The three appliance suites are unchanged at 0/0/0 over and 49/6/0 unsafe.

* **The warning that a pool's reserve is too small for this box's workers had
  never been printed by anything, and the lab that owns reserves had never been
  run with workers**
  ([#370](https://github.com/andrediashexa/hexarouting/issues/370)).
  `bpa_reserve_warn()` tells an operator that some of this box's workers hold
  no reserve at all, so the subscribers those workers own **can never be given
  a second block**: they fill their block and stay full, on a box whose
  `show bpa pool` reports a reserve. `tools/plugin-says.sh` over the 89-box
  corpus found its three rows unprinted, together with the `no stride fits this
  pool at %u workers` branch beside them - four messages, absent by `grep` and
  not merely unattributed.

  **Nothing produced them because no run had both halves.** The guard needs a
  reserve smaller than the worker count, a reserve is refused on a dynamic
  pool, and `lab/detreserve.sh` - the only lab that sets one - was not in
  `BOTH_WAYS`, so it had only ever run on a box where `n_workers ? n_workers :
  1` compares one with one.

  **That absence was never a decision, and the measurement says the lab holds
  under sharding.** `detreserve` carried no `# NOT-BOTH-WAYS:` marker, the way
  `lab/crossaddr.sh` does for counts the free list's sharding would change;
  the list simply did not name it. Run with workers it was `LAB PASSED` with
  all fourteen checks green, and it holds for a reason rather than by luck: the
  subscribers it picks and the reserve blocks they want land on the same worker
  by the same modulo at any worker count, which its header now sets out. It is
  in `BOTH_WAYS`, and that run is where the warning is now produced - by the
  pool the lab already builds, not by one arranged to make the box talk.

  **The three numbers are asserted, not the sentence.** How many workers hold
  no reserve must be the box's own worker count less the blocks the pool holds
  out of the formula, read off `show threads` rather than written down; the
  reserve to raise to must be that worker count; and what one more reserve
  block per address costs must be the outside set's size. A case asserting only
  that the warning appeared would go green on a warning whose arithmetic is
  wrong, and the arithmetic is what an operator acts on. The control is the
  lab's own second pool, whose reserve of four blocks does reach every worker
  and which must draw no warning at all. All of it is guarded on the pool
  having been created and the box having workers, because on a box with neither
  "the warning did not appear" is true for a reason that is not about the
  product.

  **Shown failing.** `bpa_reserve_warn()`'s guard made unreachable - a
  condition that can no longer be true, not a deleted line - and rebuilt: six
  checks red, each naming what it did not see, and the control still green
  because a control that moves with the mutation is not one. Restored and
  re-run: green again. The off run still goes red on exactly its floor of ten,
  and the worker-less run says in the transcript why it measured nothing.

  **`no stride fits this pool at %u workers` needed a different pool, and says
  why.** A stride is inert over a reserve - `bpa_dyn.c` skips it, because a
  reserve index is a different space - so one pool carrying both would be an
  arrangement the product itself ignores. `lab/dynamic-workers.sh` already
  drives the other branch of that same advice line on a pool with 126 mappable
  blocks, where an even stride is in reach; the case added beside it is a pool
  whose public address holds two, where the only stride that fits inside the
  address is the unreachable one and the box has nothing to name. Both branches
  are now driven in one run, and each asserts that the other's sentence is
  absent.

* **The appliance printed rows of 83 to 141 columns to a boot console that
  cannot reflow, and 2,143 real rows had never been measured**
  ([#367](https://github.com/andrediashexa/hexarouting/issues/367)).
  `appliance/hexarouting-install`, `appliance/bind-dataplane-nic` and
  `appliance/apply-tuning` print at boot, to a serial line or a VGA text
  screen: 80 columns, hard, no reflow and no scrollback, and the installer is
  read by somebody **while they decide what to type**. #359, #362 and #363 put
  the 79-column budget on the VTY; nothing had ever measured a row of the
  appliance.

  **The corpus can be kept now, and is off by default.** `test/say-coverage.sh`
  already holds every line every box printed, in a `mktemp -d` that dies with
  the suite - right for a check and useless for a measurement, so the figure
  above could only be taken by patching `say_box` in a scratch tree.
  `SAY_CORPUS=<dir>` writes the rows out, one file per program beside a `.src`
  naming the tree they came from, and the report says how many were kept and
  where. Nothing is written when nobody names a directory: a suite that writes
  outside its own tempdir on every run is a suite that leaves litter in
  somebody's repository. [#366](https://github.com/andrediashexa/hexarouting/issues/366)
  wants the same of `tools/run-all-labs.sh` and this is the shape to copy.

  **The reader is #363's method one component over.** Per message, driven
  rather than read: either **bounded** - every value that can reach it comes
  from somewhere with a widest member, so the row is rebuilt with that member
  and measured - or **unbounded but safe**, where the value is last on its row
  and a fold costs nothing, or **neither**, which is refused. The widest
  members are read from the product: the paths the program itself names, the
  drivers it declares, the ten tools it checks for, the three fields the VTY
  writes into `HEXA_DATAPLANE_MGMT`. A `fold` over everything would have been
  wrong: `say_list` folds a list and a verdict is one row, and the fix for a
  row that does not fit is to move the VALUE off it.

  **Most of what looked too wide was the rig, not the box.** `ask` prompts with
  `printf` and no newline, so a prompt and the message after it arrive in one
  captured line - `Gigabytes for connection records [30]: error: the mirror has
  to be a SECOND disk` is two rows on a box and one row in the corpus. The
  reader separates them by deriving which printers leave the cursor where it
  was and what they write after the question, so the refusal is measured from
  its own `error: ` rather than from the far side of somebody else's prompt.
  Three more were long only because a suite points the program at a tempdir:
  every one of these takes a root so a rig can hand it a tree shaped like
  `/etc`, and at the widths a real box gives them the same rows are 20 to 30
  columns narrower.

  **Fourteen rows moved the value off the row.** At the widest value that can
  reach them, before and after:

  | row | was | now |
  |---|---|---|
  | `missing tools:` with all ten missing | 141 | 49, then the list folded |
  | `update-grub missing: apply the command line from <file>` | 114 | 62 and 52 |
  | `with the management path the box recorded: <record>` | 101 | 54 and 60 |
  | `live filesystem not found at <path>` | 99 | 55 and 43 |
  | `fstab: UUID=<uuid> is mounted at boot` | 97 | 63 and 50 |
  | `network: <path> was not written` | 90 | 68, 53 and 46 |
  | `bind: <addr> is named in <file>` | 88 | 52 and 53 |
  | `the gateway <a> is not on <b>'s subnet` | 88 | 70 and 53 |
  | `no kernel in <mnt>/boot` | 88 | 64 and 24 |
  | `bind: <driver> is not available on this box` | 86 | 68 and 74 |
  | `the installed system did not verify` | 84 | 70 and 10 |
  | `setting the root password failed` | 83 | 69 and 17 |
  | `this medium carries ssh host keys` | 81 | folded at 72 |
  | `REFUSED <var>=<value> in tune.env` | unbounded | 77, value alone |

  The last of those is the one the product had already argued for and not done:
  `apply-tuning` says in its own comment that a refused value is *"quoted and
  printed whole on a line of its own"* because *"a value that pushes the
  sentence off the edge takes the reason with it"*, and it was printing the
  value in the middle of the sentence with the path to the file behind it.

  **What is left is named on every run.** 49 rows of the installer and 6 of the
  binder carry a value with no widest - a disk somebody named, a card name, an
  answer somebody typed - with the words of the program behind them. They are
  printed with their line and the columns before the value, under a ceiling
  that may only go down, because an allowlist is read once and grown forever.
  `appliance/apply-tuning` is at zero on both counts.

  **And two branches nobody had run.** Reaching the prompts meant taking
  printers called inside a `$(...)`, which is how every question in the
  installer is asked; the message reader then wanted a box for `Hostname` and
  `Timezone`, and there was none - every case here, every lab and every
  unattended install answers those from a file. They are the first two
  questions an operator meets at an interactive install.

* **Half of the audit trail's tamper message had never been printed by
  anything.** `bpa_audit.c` builds it as `%llu entr%s removed from the middle`
  with the suffix chosen by `this_seq - expect == 1 ? "y was" : "ies were"`,
  and every case in `lab/audit.sh` removed exactly **one** line - so the plural
  branch had never run anywhere.

  That is the screen somebody reads when they are asking whether the
  configuration trail was altered, and the half nobody had seen is the half
  about the worse case. `lab/audit.sh` removes two adjacent entries now, so
  the sequence jumps by two and the arithmetic is exercised along with the
  wording:

  ```
  the sequence jumps from 2 to 4: 1 entry was removed from the middle
  the sequence jumps from 2 to 5: 2 entries were removed from the middle
  ```

  Two adjacent and not two far apart, because a verifier that stops at the
  first gap would print `1 entry was` again. And the singular is asserted by
  name now as well: `removed from the middle` is in both messages, so without
  the `entr[y|ies]` half the two cases are two assertions about the same word.

* **The README said the configuration trail's every entry names the operator,
  and the product has a shape where there is nobody to name.** A change typed
  at the dataplane's own CLI is recorded `by unattributed via cli`, and
  `bpa_audit.c`'s own comment calls that the shape *"that matters"*: leaving
  the field off instead would make such an entry indistinguishable from one
  written before attribution existed, and from one where nobody was
  accountable.

  So the product's property is a **different and stronger** one than the row
  claimed - every entry says who asked, and an entry that cannot be attributed
  says so - and the absolute was the one thing a reader would rely on when
  deciding whether the trail can answer *who*. It cannot, for the change
  somebody makes to go around the VTY, and now it says that where they will
  see it. `docs/logging.md` already documented all three shapes correctly;
  only the README overstated them.

  Held from the C rather than from a second sentence: if `bpa_audit.c` writes
  `by unattributed`, the README may not claim every entry names the operator,
  and if that path ever goes the check goes red so somebody reads both again.
  Shown failing both ways.

* **The README told a reader that every release was built from its tag and
  booted before publishing, and that is false about a third of what is
  published.** It is the state table somebody reads to decide whether to trust
  this product, and that row was in the present tense about all of it.

  Measured: `tools/release-gate.sh` was added by
  [#100](https://github.com/andrediashexa/hexarouting/issues/100), and
  `git tag --contains` puts the first release carrying it at **v0.28.0**. Of
  the 74 published releases, **26 are older** - v0.2.0 through v0.27.0, every
  one of them still downloadable, and 20 of those 26 publish an ISO.

  The row names the boundary now, and says what the gate actually does, which
  is more than the old sentence claimed: built from the tag, booted, installed
  to a disk and booted again from that disk.

  And `test/test_doc_copies.py` reads that version back **out of the git
  history** rather than trusting the sentence, because a boundary somebody
  writes down is a boundary that drifts and this one already had. Not a count
  of published releases: that needs the network and `make test` runs offline
  ([#317](https://github.com/andrediashexa/hexarouting/issues/317)). What is
  held is the half that goes wrong by editing a document. Shown failing both
  ways - the wrong version named, and the version taken out again.

* **A message-coverage reader that saw one of the three bodies the plugin
  prints from.**
  [#366](https://github.com/andrediashexa/hexarouting/issues/366) gave
  `src/plugins/bpa/bpa_cli.c` a reader that hunts every message the plugin can
  print inside a corpus of real lab output and names the ones no box ever
  produced. It read the **format string** of a `vlib_cli_output` call and
  nothing else. Two more bodies sat beside it, and together they are bigger:

  ```
  vlib_cli_output format strings   87 sites, 67 readable, 66 distinct
  literals passed as an ARGUMENT   45 branches with a literal, 22 readable
  format() format strings          88 sites, 67 readable
  ```

  **The argument body is where the branches are**, which is why it matters
  more than its size. `cond ? " - PINNED by a forward, so no hold-down will
  take it" : ""` tells an operator why a block they expected back has not come
  back; `"   BELOW the floor " rfc " asks for"` is the warning that stops
  somebody setting a timeout that breaks the protocol. Both reach the screen
  as a value handed to a `%s`, and #366's rule is written on the format string
  with values cut out - so each of them was a branch reported as covered
  because nothing had ever gone to look at it.

  **The floor decides which arguments are sentences, and no second rule was
  written for it.** An argument branch is kept when its literals, cut the way
  a format string is cut, carry the same `SAY_MIN` characters of their own. A
  format fragment is only the limiting case: `"%s%s"` cuts to nothing, `" "`
  to one character, `", "` to two, and none of the three can be hunted for in
  a run. A separate "is this a format fragment" test would have been a second
  spelling of the floor. Under-floor branches are still rows, counted and
  named as unreadable - text that reaches the screen and cannot be recognised
  is not the same as text that is not there. One message per BRANCH, because
  `a ? "  (pinned by a forward)" : (i ? "  (extension)" : "")` puts three
  answers on one line of source and no run carries two of them at once.

  **Both halves moved together**, which is #366's own rule kept rather than
  waived: a denominator that moves without the evidence moving is how a
  measurement stops meaning anything. `format` joins the default set of
  printers - `show bpa pool` and `show bpa config` are written entirely that
  way and are the two screens an operator looks at first - and the corpus
  behind the number went from #366's 13 labs to the **whole 88-box suite** in
  the same change. The accounted fraction rose while the denominator more than
  doubled:

  ```
                        #366, 13 labs        now, 88 labs
  vlib_cli_output           36 of 66            47 of 66
  argument literals          not read           18 of 21
  format()                   not read           51 of 67
                          -----------        ------------
                            36 of 66           116 of 154
  ```

  The 38 still without a box are named on the screen with their line, as they
  were before - there is no allowlist, and the list is the point. Most of it
  is one shape: twelve are per-pool and global timeout overrides in
  `show bpa config`, which no lab sets and then reads back.

  Found by the widened reader and **not changed**: `bpa_cli.c:2899` can never
  print `on`. The running-configuration emitter guards each ALG line with
  `(p->alg & BPA_ALG_sym) != (BPA_ALG_DEFAULT & BPA_ALG_sym)`, and
  `BPA_ALG_DEFAULT` in `bpa_pool.h` has all five bits set, so a departure from
  the default is always an ALG switched **off**. The branch stays: it is what
  keeps the emitter correct on the day an ALG ships defaulting off, and
  deleting it would make the running configuration silently wrong then.


* **A document check whose verdict depended on whether somebody had built.**
  `test/test_doc_commands.py` refuses a document that sends a reader to a path
  the tree does not have - a file renamed or removed with the document left
  behind. `docs/roadmap.md` gained a mention of `test/bpa_vectors.h` with the
  C sweep, and that file is **generated**: 21,072 lines from
  `ref/gen_vectors.py`, listed in `.gitignore`, removed by `make clean`,
  remade by `make test`'s own `vectors` prerequisite.

  So the check was green in a working tree where a build had left the file
  behind and red in a clone made the same minute:

  ```
  $ git clone --no-local . /tmp/fresh && cd /tmp/fresh
  FAIL: docs/roadmap.md sends a reader to 'test/bpa_vectors.h' and the tree
        does not have it
  ```

  **A clone is what `tools/release-gate.sh` builds a release from.** It
  survived only because `make test` runs `vectors` first, so the one path that
  matters made the file before the check looked for it - which is a check
  passing by accident of ordering.

  A build product is not a file that is gone, and the rule's whole subject is a
  document left behind. The accepted paths are **read out of `.gitignore`**
  rather than listed here, because a second list of build products is the same
  truth in two places with nothing comparing them; only literal entries count,
  not globs, so `hexarouting*.iso` cannot excuse a name near it. Every path
  accepted that way is **printed** on every run, so the list growing a name
  nobody recognises is on the screen rather than inside the check's logic.

* **A suite that went red on correct work, depending on where the tree was
  checked out.** `test/test_installer.sh` runs the installer inside a mount
  namespace that replaces `/tmp` with a directory of its own, so the stubs on
  `PATH` are the ones the case arranged. Its own comment says the rig was put
  under `/var/tmp` for exactly that reason: *"a rig that lived under /tmp
  would be hidden by its own mount"*.

  The **program under test** is reached by its path too, and a checkout under
  `/tmp` is hidden by the same mount:

  ```
  $ git clone --no-local . /tmp/gateprobe && cd /tmp/gateprobe
  $ sh test/test_installer.sh
  FAIL: the refusal does not name update-grub by itself ...
        sh: 0: cannot open /tmp/gateprobe/appliance/hexarouting-install
  17 FAILURES
  ```

  The same clone under `/home` is green. Seventeen failures that read as
  product defects and are a property of where somebody put the tree.
  `tools/release-gate.sh` clones to `/tmp/hexa-release`, so that is not a
  hypothetical location - the gate does not run this suite today, and nothing
  says it may not.

  The hazard had been seen and half fixed: the rig moved off `/tmp`, the
  installer's own path did not. Both invocations that run inside such a
  namespace now run a **copy** under the rig, with `cmp` asserting it is the
  program - a copy nobody compares is how a suite comes to measure a stale
  binary. A copy rather than a third bind because the installer resolves
  nothing from its own directory, measured: no `dirname "$0"` anywhere in it.

* **A hole in a wiki-screen template absorbed a word the box had stopped
  printing, and 199 templates open with one**
  ([#365](https://github.com/andrediashexa/hexarouting/issues/365)).
  `test/test_wiki_screens.py` had four defences against a matcher that cannot
  fail and all four are about a template being MOSTLY hole. `{peer} does not
  keep what it received` is 33 literal characters and 60% literal on an IPv4
  address, so it passed every one of them correctly - and it opens with a
  hole, so the page's leftover `neighbour ` went into the hole whole and the
  reader was green on both sides of the change above. The question the four
  never asked is what a hole is allowed to CONTAIN.

  The reader already parses each f-string from the AST, so it knows the slot
  behind every hole. A slot that is a name, an attribute or a subscript holds
  ONE VALUE - an address, an interface, a count - and one value is one token:
  **a hole whose slot is a single value may not contain whitespace.** 1383 of
  the corpus's holes are held to that; 199 templates open with a hole and 101
  of those open on one the rule reads.

  **Measured before it was written, over the 59 screens the reader attributed
  that day:** 48 holes filled, 6 holding whitespace, and not one of the 6 a
  slot that legitimately holds a phrase. Two are a `${...}` in a shell script,
  whose slots this reader does not parse and therefore asks nothing of - they
  are named on every run under a ceiling of 2 that may only go down. Three
  were the rule's own evidence: the hole was swallowing the `error: ` marker
  or the first clause of a row built by concatenation, and with the rule on
  all three are read again tighter. The sixth is the prefix-range refusal,
  which this product builds in four goes and where the last hole was
  swallowing `8, ge 24, le 16)` whole; no two strings account for that row, so
  it moved to the printed skip list. **That is the whole cost: one screen,
  and it had never really been attributed.**

  The loud half of the rule - naming a page rather than declining to attribute
  it - runs on the VERDICT ROW ONLY. Both widths are green on the wiki as it
  stands, so the wiki does not choose between them; an `err` headline is one
  clause whose slots are an address or a count, while the paragraphs under it
  are exactly where a slot holds a sentence (`r.reason` is whatever the
  dataplane said). Failing to match is cheap, because the screen lands on the
  printed skip list; calling a correct page wrong is not. For the same reason
  the rule is NOT taken over the halves of `concat_match`: measured, five
  screens the box really prints are called wrong there.

  The four slots exempt by the word their author wrote - `reason`, `why`,
  `what`, `msg` - are a reading of this product's own vocabulary and not a
  list of sites: each was found by reading the code that composes the
  sentence, a new `f"{reason} ..."` anywhere in the tree is exempt the moment
  it is written, and how many holes each word exempts is printed on every run.
  Pointed at the wiki as it stood before the page above was corrected, the
  reader names both stale screens, the slot, and what the hole swallowed.

* **97% of what the interactive installer can say had been produced by nothing
  in this repository, and one of its refusals could not be produced at all.**
  `appliance/selftest` has had a reader since #302 that refuses a message no
  box prints; `appliance/hexarouting-install` had none, and the four suites
  that drive it all drive its ANSWER FILE, which is the non-interactive path
  ([#364](https://github.com/andrediashexa/hexarouting/issues/364)). What went
  unrun was the half an operator meets first: the disk list, both "there is no
  disk" refusals after a mistyped letter, the management-network paragraph,
  `EVERYTHING ON THE DISK YOU CHOOSE IS DESTROYED`, and every screen after the
  partitioner - "cannot mount", "unsquashfs failed", "no kernel in /boot",
  "grub-install (BIOS) failed", and the closing sentence an operator reads
  before they walk away from the machine.

  `test/say-coverage.sh` is that reader, as a holder rather than a third copy
  of it: the printers are derived from the program, the needle is the message
  with every `$var`, `${var}`, `$(...)` and backtick cut out, and a message
  added without a box goes red on the next run. `test/test_installer.sh`,
  `test/test_bind_nic.sh` and `test/test_apply_tuning.sh` each hand it their
  boxes and read the verdict at the end - 181, 37 and 30 messages, every one
  with a box that produced it.

  **`[ -n "$DISK" ] || die "no disk given"` could not fire on any input at
  all**, and is removed. Every path out of `choose_disk()` either returns
  non-zero - the empty menu, which exits above it - or prints a path, and the
  branch is only taken when `$DISK` was empty to begin with. The reader found
  it by asking for a box and there being none to arrange. What an operator on a
  machine with nothing to install onto meets is the menu's own refusal, which
  says that and what to do about it; "no disk given" would have told them they
  gave no answer, about the only answer they could give.

  Five more branches nothing had executed came out of the same sweep and now
  have boxes: `--which-driver` says which driver on either kind of box, and
  `bind-dataplane-nic` reporting a device that DID move (both in
  `appliance/bind-dataplane-nic`); and a kernel with no 1G hugepage support,
  and a box with no `update-grub` on it, in `appliance/apply-tuning`.

* **`lab/crossaddr.sh` asserted the opposite of what the box does, and was kept
  out of the suite for it.** It was written to record the defect in
  [#226](https://github.com/andrediashexa/hexarouting/issues/226) - a dynamic
  extension taken off the free stack, which under `policy interleaved` puts one
  subscriber on two public addresses - and #226 then changed the allocator to
  prefer a free block on the subscriber's own address. Every `expect` in the
  file was therefore red by construction, and `tools/run-all-labs.sh` listed it
  in `NOT_LABS` with that as the reason.

  It is rewritten against a running box, and **every number in it is one the
  box was seen to produce**: 4 of 4 extensions paired in each of four pools,
  16 of 16 over all of them, two retention records naming one address with
  adjacent port ranges, and no SIP media pair refused. Writing the new
  assertions from the allocator's own arithmetic - pairing needs a gap that is
  a multiple of the address count - would have been the fourth way a mutation
  lies in `TESTING.md`, an expected value computed by the thing under test.

  **The assertions are floors on a rate, not a guarantee, because the product
  does not make one.** The allocator PREFERS the subscriber's address and falls
  back to the free stack rather than refusing an extension, which was
  deliberate: refusing costs a customer their traffic instead of costing them a
  second address. A check demanding 100% would go red the first time a pool ran
  tight, on a box doing exactly what it was designed to do. Each arrival
  pattern must pair at least 3 of its 4, and the run as a whole may lose at
  most one of sixteen.

  **One of its own pools could not fail, and the mutation found it.** With four
  subscribers arriving over four public addresses the gap is four, four is a
  multiple of the address count, and every extension realigns onto its own
  address by accident - so the parallel pool paired 4 of 4 with the preference
  deleted from `bpa_dyn_acquire_extra()`. This is the third time that trap has
  been found in this repository on the same mechanism. Six subscribers now
  arrive and four extend, and with the preference removed the file reports
  0 of 4 in every pool and 14 checks red.

  The lab leaves `NOT_LABS` and joins the suite: 28 checks, 162 seconds. It
  keeps its `# NOT-BOTH-WAYS:` marker and the paragraph arguing it, because the
  free list is still sharded per worker and every gap it counts would otherwise
  be about a different arrangement.

* **The BGP page quoted a verdict the fix to
  [#363](https://github.com/andrediashexa/hexarouting/issues/363) had just
  changed, and the wiki reader did not catch it.** Both
  soft-reconfiguration refusals lost the noun `neighbour` from the headline,
  because the address that follows already says who is meant and the word cost
  ten columns on a row the box does not fold - with an IPv6 peer the verdict
  reached 87 columns on a console of 79.

  `test/test_wiki_screens.py` passed on the stale page, and that is worth
  recording rather than only fixing: the product builds that row as
  `f"{peer} does not keep what it received"`, so the template OPENS with a
  hole, and a hole matched as `[^\n]+?` absorbs any leading text - including a
  word the box stopped printing. 199 of the templates in that corpus open with
  a hole. The page is corrected here; the reader is
  [#365](https://github.com/andrediashexa/hexarouting/issues/365).

* **The console rule held 131 rows and said nothing about the 296 that carry a
  value, and an IPv6 peer is what makes the difference.**
  [#359](https://github.com/andrediashexa/hexarouting/issues/359) and
  [#362](https://github.com/andrediashexa/hexarouting/issues/362) put a rule on
  `src/hexash/`: no LITERAL row is wider than `config.CONSOLE_COLUMNS`. Both
  printed, on every run, the half they did not hold. The width of an
  interpolated row is not a property of the source: `neighbour {peer} does not
  keep what it received` is 48 columns plus the peer, which fits
  `200.150.0.2` and does not fit
  `2001:0db8:1234:5678:9abc:def0:1234:5678`
  ([#363](https://github.com/andrediashexa/hexarouting/issues/363)).

  `test/test_hexash_refusals.py` gains a second reader, in that file rather
  than in a new one because that is where the driving already is: its boxes and
  the 22 suites it runs reach 185 of the VTY's 185 verdict sites and 168 of its
  238 print sites, and a new harness would have had to build its own way to
  every one of them. The same hook that records WHICH refusal fired now records
  WHAT reached the console, and the reader rebuilds each row with the widest
  value that can reach it. The widest values are read from the product or
  computed, never invented: an IP address is 39 columns out of `ipaddress`, a
  prefix 43, a decimal 10 (the widest range this VTY's own grammar declares),
  an interface name 28 (VPP's longest card name plus a tag out of
  `config.VLAN_RANGE`), and a closed set is the widest member of whichever of
  the product's 50 declared collections holds it. A value with no widest - a
  pool name, a route-map name, a path, a sentence the dataplane sent back - is
  accepted only where nothing follows it on its row, which is the shape
  `say_finding` uses for its statement.

  Eight rows were fixed, each driven before and after. The two `neighbour
  <peer>` verdicts [#349](https://github.com/andrediashexa/hexarouting/issues/349)
  measured on an IPv4 peer are 87 columns on a full-length IPv6 one and 77 with
  the noun dropped. The `commit confirmed` window refusal was 136 to 139
  columns and is 48 to 50 with its reason under it: its WORDS alone were 136,
  so no value could have made it fit, and no static walk could see it because
  the f-string interpolates the number. The `router static` refusal was 84 and
  is 41 with the word typed moved into the prose. Three IS-IS punt sentences
  were 128, 95 and 111 and now go through `print_prose`, and so does the cutoff
  advice that reached 144.

  What is left is NAMED on every run against two ceilings that may only go
  down, in the idiom the refusal floor above it already uses: 26 rows that do
  not fit once driven and 52 that carry a value with no widest and the VTY's
  own words behind it. `docs/routing.md` quoted the pre-split BGP refusal and
  is corrected.

* **Seven C assertions that could not fail, and the five build recipes that
  hid a mutation from the compiler.** The dataplane plugin's ten harnesses -
  7,577 lines, 570 `CHECK (...)` sites - were the last body this repository had
  not swept for assertions that cannot fail. 108 product mutations were
  anchored to settle it, each compiling under `-Wall -Wextra -Werror`, because
  a build the compiler refuses is not a check that noticed. A static pass over
  the same files flagged one candidate and it was a false positive.

  Six findings are one shape: a fixture that answers for a second reason.
  `test_bpa_rtsp.c` asked a non-SETUP request and a non-200 reply to be refused
  with messages that carry no Transport header, so both were refused by the
  absence - green with the method test deleted, and green again with the status
  test deleted. `test_bpa_rewrite.c` asserted GRE's Call ID untouched at offset
  26, where a GRE session's delta folds to zero, so the early return could go,
  the packet fall into the UDP branch, the flags word at offset 20 be
  overwritten, and all 41 checks pass. `test_bpa_icmp.c` measured the embedded
  port from the embedded header's length with every fixture quoting a 20-byte
  one. `test_bpa_sip.c` probed the sent-by span guard from neither side and the
  port ceiling from neither side; a sent-by of `100.64.0.5x:5060` would have
  been rewritten ten bytes in, leaving `200.150.0.17x:5060`. `test_bpa_dyn.c`
  never quietened a subscriber holding an extension, so the walk over
  `d->extras` in `bpa_dyn_idle()` ran over an empty list - a block owned for
  the life of the process, visible only as a slice that stops having free ones
  - and never asked a subscriber holding two blocks for an ordinary flow, so
  `by_sub` naming the primary was a claim nothing could contradict.

  The seventh is not in a harness. Five of the ten recipes named
  `test/bpa_tcp_shim.h` and not `src/plugins/bpa/bpa_seq.h`, which they reach
  through it - so the serial-number arithmetic the FTP ALG rests on could be
  changed and `make test_bpa_ftp` would answer *is up to date*. Measured: the
  signed comparison in `bpa_seq_delta_at()` cut to a plain `>`, all five suites
  green, the 2^32 boundary cases among them. `TESTING.md` already names this as
  *a mutation that does not reach the compiler*; here it was a standing
  property of the build rather than an accident of one campaign.

  Sixteen behaviours survived a mutation with every check green and are
  recorded in `docs/roadmap.md` as limits rather than dropped: fourteen are
  cost shortcuts or guards no input can name, and two - `n_exhausted` on the
  primary acquire path, and the stride's worker-slice arm, which both stride
  sections run at one worker on purpose - are gaps a fixture could close.

* **A permission check whose fixture was a directory no box has.**
  `test/test_hexash_perms.py` held
  [#188](https://github.com/andrediashexa/hexarouting/issues/188)'s fix - that
  `save` must not narrow the configuration store out from under a non-root
  operator - by building a store at `0770`, saving into it, and asserting it
  came back `0770`. Its own comment said *"what appliance/install.sh states:
  0770 on the directory"*.

  The installer sets **2770**, and has since the setgid bit went in. The bit is
  not decoration: `install.sh` says in the comment above its own two
  `install -d -m 2770` lines that without it a revision root writes at boot is
  group root, and the next operator cannot read it - so `rollback 1` becomes a
  command that works only for whoever made the revision.

  So the case was asserting that a mode survives a save, about a mode no
  installed box carries, and it could not see the bit going missing because its
  fixture never had one. Measured, rather than argued: a one-line mutation
  making the rollback store's `push` clear the setgid bit takes the new case
  red twice and leaves the old one **entirely green**.

  The two modes are read out of `appliance/install.sh` now, by two small
  readers, so the fixture is the directory the installer makes rather than one
  this file invented. A reader that stops finding its line fails rather than
  comparing `None` with `None`, and the setgid bit is asserted **by name**
  beside the equality, because an equality against a mode read from the same
  place would go quiet the day somebody changed both.

* **A lab case that had been passing for four months on a refusal its own
  comment did not name.** `lab/commit.sh`'s *"a commit the dataplane refuses
  must FAIL"* built a pool with `outside 10.0.0.0/30` and said, in the comment
  above it, that the public prefix collides with a local address and that is
  what the device refuses. No interface in that lab carries 10.0.0.0/30 and
  none ever did. What the device was actually refusing was `block-size 500`,
  which is not a power of two.

  It surfaced when
  [#360](https://github.com/andrediashexa/hexarouting/issues/360) moved that
  refusal to the prompt: the pool then committed cleanly and all three
  assertions went red at once. **A lab that goes red when a defect is fixed is
  a lab that was measuring the defect.** Section 8 of the same file, *"the
  check still catches what the device refuses"*, had the same `block-size 500`
  and stopped reaching the device for the same reason.

  Both now use a pool the VTY passes through and the device refuses: two
  overlapping prefixes in one address set, which `bpa_addr_set_build` rejects
  because an overlap breaks the address-to-index bijection and a broken
  bijection means a subscriber can be billed as another one. Verified by
  driving all four statements through a `Shell` and seeing nothing refused at
  the prompt, which is the property the case needs and the old one had lost.

  And the case names the refusal now. `error` is green for any refusal at all
  and `doomed` is green for any refusal about that pool, which is exactly how
  it stayed green while the device refused something else: a sweep of every
  `expect_in` in `lab/` finds only two needles that generic, and this was one
  of them. The other is an `expect_not_in` where generic is the right answer.

* **The 22 screens the wiki reader could not see, gone through one at a time -
  and 13 of them it can account for now**
  ([#362](https://github.com/andrediashexa/hexarouting/issues/362)).
  `test/test_wiki_screens.py` printed its SKIP list on every run because that
  list is the honest edge of the rule. Read out loud, most of it was not an
  edge: **six screens were invisible to a defect in the reader's own corpus.**
  It joined a shell call's source lines until the double quotes closed, and a
  COMMENT with an odd number of quotes in it - `# effect" must not collapse
  into "in effect"` - is not an unclosed string. Reading on from that one
  comment swallowed 247 lines of `appliance/selftest`, the account checks and
  the whole tuning report among them, and 92 lines of
  `appliance/bind-dataplane-nic`. Only a call is read on from now, and the
  self-test's three account screens and three tuning screens are attributed
  against the script that prints them.

  **Six more were reachable by driving**, which beats matching: the BGP
  `linux_cp` naming warning (built in three goes, so no pair of strings
  accounts for the row), the AS-path regex FRR would refuse, the HA mapping
  divergence, both shapes of the `commit cutoff` refusal, and the `router bgp`
  whose daemon is not running. That last one, and the port range this branch
  adds to the pool page, are the first screens here driven with anything under
  the VTY at all: both are decided before a message is sent, and what is
  stubbed answers "I am here" and nothing else, so no word on either screen
  comes from the stub. **22 skips became 9; driven matches went 18 to 25 and
  template matches 23 to 34.**

  **The nine that remain are a limit and are named as one.** Every one was
  driven or rendered by hand and is what the box prints today: the self-test's
  statistics segment, the handover failure and the manifest failure, the
  account failure's headline, the RFC timeout floor, the remap refusal on two
  pages, the revert that could not be armed, and the file `--load` refuses.
  They fail the same way for the same reason - the interpolated value is more
  than half the row, or the row IS the value (a file name, a path, a pair of
  addresses) - and loosening `LITERAL_SHARE` to take them is loosening the one
  bound the whole file rests on.

* **Two wiki screens that were not what the box prints.**
  `acesso-login-e-papeis.md` quoted the self-test's unusable-role failure with
  its three continuation rows one space too far right: the page drops the two
  columns the marker carries, and that screen dropped one. Driven against
  `appliance/selftest` with a fake root, the reason sits at six columns under a
  marker at zero, not seven.

  `configuracao-de-pool.md`'s block-stride warning was hand-folded into six
  rows of about 74 columns. **The dataplane writes three** - 177, 42 and 107
  columns - and folds none of them, so the screen on the page had never been on
  a screen. That is the failure this reader exists for, arriving from the other
  side: the sweep of 2026-09-15 found eight pages quoting a refusal the product
  had SPLIT as one long sentence, and this is a page splitting rows the product
  writes whole. Corrected, the reader attributes it to the plugin.

* **`ports` was documented with an argument it does not have, and with none of
  its refusals.** The statement table in `configuracao-de-pool.md` showed
  `ports <min> <max>` with an empty argument column. The VTY offers
  `<1-65535> <1-65535>` and refuses on four counts, each driven and quoted:
  either end outside the range, naming WHICH end; a value that is not a number
  at all, answered by asking for one; two legal ports in the wrong order, which
  is a different refusal because the range is not what is wrong there; and at
  `commit`, a range too narrow to hold one block, which needs `block-size` as
  well and so cannot be answered on the line. The last one is now driven by the
  reader too, so the page cannot go stale unnoticed.

* **A word edited inside a screen only DRIVING can attribute went unreported.**
  `diverges()` reads the paragraphs under a verdict only once the verdict has
  been recognised as the box's, and it recognised one by template alone. The
  refusals whose headline is mostly interpolated - `error: router bgp 65001:
  bgpd is not running` is sixteen literal characters out of thirty-seven - have
  no template and never will, so an edited word in the reason under them
  dropped the screen quietly onto the skip list instead of failing. A verdict
  row that came out of one of the runs is now also the box's row. Measured:
  one word edited in each of the seven screens newly driven here was reported
  once before and is reported three times now, and the other four still land
  on the skip list - the edit is in a fold continuation, or in a sentence the
  box opens with a value.

* **Three wiki screens the new reader caught, two of them made stale an hour
  earlier by this same release.** `test/test_wiki_screens.py` went in green and
  then went red on its first real test: the split of the `nat: a hierarchy, not
  a block` verdict
  ([#362](https://github.com/andrediashexa/hexarouting/issues/362)) left
  `configuracao-de-cgnat.md` quoting a row the box had stopped printing, and
  the same commit's rewrite of the FRR-silent refusal left a section HEADING in
  `commit-rollback-e-save.md` that was the old verdict, character for
  character. A heading is the worst place for it, because a heading is what
  somebody searches for after reading the verdict on their console.

  That is the whole argument for the reader, and it arrived without being
  looked for. Ten screens went stale over the last weeks and every one was
  found by reading pages one at a time; these two were found by a suite, in the
  same session that broke them, before anybody published them.

  The third came out of the reader's SKIP list rather than out of a failure,
  which is why that list is printed rather than counted: the `linux_cp` naming
  warning in `configuracao-de-bgp.md` was quoted in the pre-#271 shape, the
  statement and the reason glued into one sentence and hand-wrapped at column
  0. Driven, `say_finding` prints the reason first and wrapped, with the
  statement whole on its own last row - the statement is what an operator greps
  for and types back, so it must not be folded, and the reason is what they
  read, so it must be. Nothing could attribute the page's version to any
  template, which is how it surfaced.

* **Seven refusal verdicts did not fit the console, and the verdict is the one
  row `err` must never fold**
  ([#362](https://github.com/andrediashexa/hexarouting/issues/362)).
  `err` prints `error: <msg>` and **wraps nothing**, on purpose
  ([#252](https://github.com/andrediashexa/hexarouting/issues/252)): the first
  row of a refusal is the verdict and an operator reads it whole. Those seven
  characters are also seven the source does not show, so a 75-column string is
  an 82-column row. Measured over the `err`/`err_prose` sites whose message is
  fully literal, seven reached the console at **83 to 217 columns** against
  `config.CONSOLE_COLUMNS`, which is 79 - and a verdict row that folds is worse
  than a prose row that folds, because the one sentence the reader was meant to
  take in at a glance is the one the terminal breaks.

  Six shorten the way
  [#349](https://github.com/andrediashexa/hexarouting/issues/349) and
  [#355](https://github.com/andrediashexa/hexarouting/issues/355) shortened
  theirs: the verdict keeps only the refusal and the rest goes under it through
  `print_prose`. `configure` on a silent dataplane now says *the dataplane is
  not answering* at 37 columns with its consequence on the row below, which
  makes that screen consistent with itself - the three rows under it were
  already wrapped by #359. *This box is not running from the installation
  medium*, *nat: a hierarchy, not a block* and the SNMPv3 credential refusal
  split the same way, the last one putting `auth <protocol> <passphrase>` and
  `priv <protocol> <passphrase>` on a row each, because a shape the operator is
  meant to retype must not be handed to `err` inside a sentence.

  **The FRR-silent refusal had two owners**, written out at both the commit
  check and the commit itself, and a sentence with two owners is a sentence
  that becomes two sentences the first time one of them is reworded. It is one
  object now, `FRR_SILENT_REFUSED`, and both sites print it.

  **The seventh is not a reflow and was not treated as one.** `nat cgnat
  logging`'s usage line listed five alternative spellings in one sentence
  separated by pipes, at 217 columns; folded to 79 that is four rows of
  pipe-separated fragments, which is harder to read than the unfolded row and
  not easier. It is now one spelling per row, **built from `CGNAT_CONFIG`** -
  the table `?` and TAB answer from, and the same move the `cgnat <word>`
  refusal already makes. A usage line typed out by hand is a second copy of the
  grammar, and this one had already drifted: it offered `nat cgnat logging udp
  disable` and `nat cgnat logging tcp disable`, which `?` offered neither of.
  `?` now carries `| disable` on both hints, so the refusal and the completion
  cannot disagree. The verdict names no word on purpose - the branch is reached
  by `cgnat logging sideways` and by `cgnat logging udp` with nothing after it,
  and `udp` **is** a spelling.

  **The rule is on the file.** `test/test_console_width.py` gained the same
  `ast` walk over the same five modules for `err`, `err_prose` and
  `err_dataplane` calls whose message is fully literal, measured with the
  prefix **read out of `Shell.err`** rather than written into the check, for
  the reason the budget is read out of `config`: a second copy of `error: ` is
  a second thing to keep in step, and one that fails silently. 67 verdict rows
  are measured. What stays open is the **118 verdicts whose headline
  interpolates a value**, still printed as a count on every run and still the
  half #359 left open.

  Shown green, then red with one verdict put back as a single row - the check
  names it at 91 columns - then green again; and, separately, the extended
  check run against `chore/console-width-sweep`'s `src/hexash` in a scratch
  tree, where it names all seven and nothing else. Needles moved in `test/test_hexash_refusals.py`, which now asserts
  the verdict row rather than the whole sentence, and asserts the logging
  shapes against `CGNAT_CONFIG` rather than against a list of its own; the
  `nat` transcript in `docs/routing.md` shows the two rows the box prints now.

* **Fifteen more refusal and report rows were wider than the console, and
  nothing swept the file**
  ([#359](https://github.com/andrediashexa/hexarouting/issues/359)).
  [#355](https://github.com/andrediashexa/hexarouting/issues/355) split seven
  refusals of this shape and fixed the seven it had measured. A static sweep of
  `src/hexash/hexash.py` afterwards found **fourteen literal `print()` rows
  still at 82 to 135 columns** against `config.CONSOLE_COLUMNS`, which is 79:
  the two under `configure` on a silent dataplane, the one under `show
  interface utilization` that says why a single sample is not a rate, both
  utilisation footers, the RPKI "no cache connected" note, the forwarding-table
  note, both OSPF Exchange rows, three of the IS-IS punt rows and both overload
  rows. They now go through `print_prose`, each keeping the indent its site
  already used.

  A fifteenth was invisible to that sweep because it is an f-string: the
  `configure` permission refusal printed *membership of hexa-cgnat allows CGNAT
  configuration, hexa-admin allows everything* bare, at **85 columns**, while
  `start shell` printed the same sentence through `print_prose` and wrapped it.
  Two refusals about one fact disagreed about whether they fit the console.

  Driven rather than read - `configure` on a box whose dataplane is not
  answering is the screen a VM meets while it is still starting, and the advice
  *'show version' says which daemons are up, and this usually clears within a
  minute of boot* was at the END of the 129-column row, which is where an
  80-column terminal folds it.

  **The rule is on the file, not on fifteen screens.**
  `test/test_console_width.py` parses every module of `src/hexash` with `ast`
  and fails on any fully literal `print()` row wider than
  `config.CONSOLE_COLUMNS`, read from the module rather than written into the
  check. It prints what it does NOT claim on every run, because a rule read as
  wider than it is, is how there came to be fourteen: **178 `print()` calls
  carry an interpolated value** and **185 refusal verdict rows go through
  `err()`**, which prefixes `error: ` and so reaches the console seven
  characters wider than the source shows. Those depend on what is interpolated
  - an IPv6 neighbour is 39 characters where an IPv4 one is 15 - and bounding
  them needs the widest value each site can hold, which is a separate problem
  that #359 leaves open and #276 solved for `say_finding` by driving it.

  Shown red: the overload rows put back as bare `print()` calls, and the check
  names both, at 93 and 135 columns. One needle moved. The IS-IS punt
  assertion in `test/test_hexash_igp.py` was cut from a sentence that now folds
  between `kernel` and `is unknown`, so it matches the flattened text the way
  the apply, BGP and dataplane suites already do: what is asserted is the
  sentence the operator reads, not the row the terminal breaks it on.

* **`ports` in a pool was enforced by nothing at all**
  ([#361](https://github.com/andrediashexa/hexarouting/issues/361)). Every
  other numeric statement in the pool block got a bound with
  [#350](https://github.com/andrediashexa/hexarouting/issues/350) to #354.
  `ports` got none, and it is the one carrying two numbers and a cast: a max
  below the min, both ends above 65535, port 0, a value above a u32 and a
  negative one were all taken at the prompt and staged into the candidate.

  ```
  ports 9000 100                 TAKEN
  ports 70000 80000              TAKEN
  ports 0 65535                  TAKEN
  ports 4294967296 4294967297    TAKEN
  ports -1 100                   TAKEN
  ```

  `port_min` and `port_max` are `u16` in `bpa.api` and in `bpa_pool_t`, so
  `ports 70000 80000` was saved and arrived at the dataplane as `4464-14464`:
  the pool that commits is not the pool that was typed, and nothing on the far
  side can name 70000 because by then 70000 is not the number any more. That is
  [#352](https://github.com/andrediashexa/hexarouting/issues/352)'s cast defect
  in a second statement. The ordering half was caught only by `bpa_pool.c:148`,
  on the far side of `commit`, with the candidate already built.

  Three refusals, because they are three different mistakes and one message
  for all three would tell an operator the wrong thing about two of them:

  ```
  error: invalid argument: ports min 70000: expected 1-65535
  error: invalid argument: ports 9000 100: the max is below the min
  error: pool 'narrow': block-size 512 exceeds the 101 usable ports
  ```

  The ordering is said as an ordering and not as a range, because `ports 9000
  100` breaks neither end's bound and a refusal quoting `1-65535` would send
  the operator off to check two numbers that are both fine. The widest is 67
  columns, inside the 79 a serial console has.

  The third one is asked at `commit` and **not** at the line, and that is the
  one decision in this change rather than a consequence of it. It needs
  `block-size` as well as `ports`, and the two can be typed in either order:
  `ports 1000 1100` followed by `block-size 64` is a correct pool built in a
  correct order, and a guard at either line would have refused its first half.
  `Pool.block_fits()` is therefore asked once, of the finished pool, by the
  commit check - of the pools the candidate DEFINES and not of every pool in
  it, because a pool the box already has is a fact and refusing a commit over
  one this change does not touch is how a box stops being configurable.

  The two line-level guards are at the typed-statement site in `do_pool` and
  **not** in `Config._pool_line()`, for the reason
  [#357](https://github.com/andrediashexa/hexarouting/issues/357) and #360
  wrote down beside `block-size`: that function is also the reader for the
  configuration read back out of the running dataplane, and a port range the
  box already has is a fact rather than a request. The check asserts that a
  configuration carrying `ports 9000 100`, or `ports 70000 80000`, still
  parses with nothing in `unknown`.

  `?` quotes `<1-65535> <1-65535>` now. It said `<min> <max>`, which was at
  least honest while nothing was enforced, and is the gap #350 to #354 were
  about once something is.

  Driven through the VTY and read back out of the candidate, which is the
  shape #350 to #354 established - a case that calls the predicate directly
  proves the predicate and not the path. Taken red by removing the guards at
  their call sites, which leaves the messages they are cut from untouched:
  27 failures in the VTY suite and 3 in the commit-check suite, with all five
  values staged where the dataplane will not build them.

* **`?` promised a power of two and nothing in the VTY kept it**
  ([#360](https://github.com/andrediashexa/hexarouting/issues/360)). The hint
  on a pool's `block-size` says *"ports per block, power of two"* and quotes
  `<64-32768>`. [#352](https://github.com/andrediashexa/hexarouting/issues/352)
  enforced the range and left the shape to nobody, so `block-size 100` was
  taken at the prompt, staged into the candidate, written to the file, and
  refused on the far side of `commit` by `bpa_pool.c`: *"pool 'p': block size
  100 must be a power of two"*. The whole argument for modelling a statement
  in the VTY is that the operator is told no at the point of typing rather
  than at commit time, with a candidate already built that was probably going
  to change something else too.

  The prompt refuses it now, and names the two values on either side, because
  *"not a power of two"* leaves an operator doing arithmetic at a prompt:

  ```
  error: block-size 100 is not a power of two: the nearest are 64 and 128
  ```

  78 columns at its widest value, inside the 79 a serial console has.

  The guard is at the typed-statement site in `do_pool` and **not** in
  `Config._pool_line()` - which is where the next person adding a constraint
  to this statement will reach, because that is where the statement is
  modelled. That function also reads the configuration back out of the
  running dataplane, and a value the box already has is a fact rather than a
  request; a bound put there is
  [#357](https://github.com/andrediashexa/hexarouting/issues/357), where a box
  carrying `block-size 4` stopped being committable at all. The reason is
  written beside the guard, and the check asserts that a configuration
  carrying `block-size 4`, and one carrying `block-size 100`, still parse.

  `Pool.POW2` is a set beside `Pool.RANGES` rather than another row inside it:
  a range asks whether a number lies between two others and this asks what
  shape it has, and one table answering two questions is a table
  `check_range` would have to know which question it was being asked.

  Driven through the VTY and read back out of the candidate, which is the
  shape [#350](https://github.com/andrediashexa/hexarouting/issues/350) to
  #354 established - a case that calls the predicate directly proves the
  predicate and not the path, and that is how #357's first two attempts could
  not fail. Taken red by removing the guard: four values inside the range are
  staged where the dataplane will not build them.

* **The three large engineering documents said six things the box does not
  do.** `docs/claims-sweep.md` named its own gap - `docs/architecture.md`,
  `docs/routing.md` and `docs/logging.md`, 7,236 lines, never read end to end
  against the code - and that sentence stood while every release changed what
  they describe. Read now, and the six are two shapes: a transcript nobody
  re-took, and a number nobody recomputed.

  The one that costs an operator most is a **virtio guest being told it has no
  NIC to choose from**. `routing.md` said a guest's NICs fail the PCI test, and
  printed a `show hardware` message saying so on the screen an operator reaches
  when the box forwards nothing. `pci_address_of()` walks **up** the sysfs
  parent chain for exactly this reason, and reproduced in a tempdir,
  `/sys/class/net/x/device -> .../pci0000:00/0000:00:12.0/virtio3` resolves to
  `0000:00:12.0`: the card has been eligible since
  [#286](https://github.com/andrediashexa/hexarouting/issues/286) and the box's
  own message now says so. Beside it, the same page's `show hardware`
  transcript still quoted `Set HEXA_DATAPLANE_IF in /etc/hexarouting/tune.env`
  - the advice [#283](https://github.com/andrediashexa/hexarouting/issues/283)
  replaced, in the section that is about #283 - where the VTY now names
  `configure`, `dataplane interface <name>`, `commit`. A VM with one virtual
  NIC is, by that page's own sentence, how most people first meet this product,
  and both readings were wrong for that box and right for nobody.

  Then: `show cgnat ?` listed eleven sub-commands where the tree offers twelve,
  an interface block's `vrf` description was stale in both places the page
  prints that listing, the reserve's cost under `blocked` said **738 of 882
  moved and 21 dropped** where the model it cites gives **750 and 7**, and the
  memory table costed the dynamic allocator at **17 bytes per block** across
  four arrays where `bpa_dyn.h` now has five and **21**. All six were
  documentation defects: where a page and the code disagreed, the code was
  right every time.

  **Two readers rather than two more copies**, in `test/test_doc_copies.py`. A
  `?` listing a document prints is compared to `Shell._options()` - the option
  names and their descriptions, which is where `ha` and `vrf` slipped;
  `test/test_doc_commands.py` walks the **command** on the prompt line and
  stops at the placeholder, and what follows it is output nothing read. And the
  reserve table's four numbers are recomputed by running `ref/bpa_model.py`,
  not grepped out of `ref/validate.py`'s output, so the number has to be what
  the arithmetic says. Shown red five ways between them, including the prompt
  line reworded so the listing check would otherwise have gone quiet.

  What was checked and held is in `docs/claims-sweep.md` with the rest: 149
  backticked VTY commands walked through the command tree, 183 identifiers and
  every link and anchor resolved, `show hardware` driven through all four of
  its states against `/sys` trees in a tempdir, and the RFC floors, the IPFIX
  record's seven fields, the console budget and the labs' off-run floors read
  from whatever owns each. What needed a dataplane, a NIC or a booted medium is
  recorded there as out of reach rather than passed over - the lab suite was
  running on another tree throughout.

* **Three more labs waited out a collector window chosen when they were
  written.** `tools/ipfix_collector.py` takes a number of seconds, and a lab
  that picks one and then waits it out has decided, at writing time, how long
  the box would take. On a machine carrying something else the box is slower,
  its records arrive after the window has closed, and the lab reports that the
  log is missing them - a product defect the product has not got. That is
  [#342](https://github.com/andrediashexa/hexarouting/issues/342) in the soak,
  and on 2026-09-15 it was `detreserve` at 30 s, `extblocks` at 25 s and
  `ipfix` at 75 s, all three red on *"the record arrived at the collector"*
  while the same machine ran the whole test suite under `gcov` three times
  over.

  All three now tell the collector when the box has finished, behind a ceiling
  of 300 seconds for a run nobody is watching. `test/test_lab_collector_window.py`
  holds the rule, and it needs no list of exceptions: **a lab that starts the
  UDP collector and waits on it must either pass a stop file or give it a
  budget generous enough not to be the thing that expires**. Both spellings are
  in the tree - these three tell it, and `ipfix-stop` and `ipfix-outbuf` give
  it 300 seconds and synchronise on the box's own exported record count. What
  is refused is the third: a number small enough to be reached. The labs the
  suite does not run at all are read out of `NOT_LABS` rather than listed
  again, and dropping that table takes the check red.

* **The lab suite told the reader to look at the machine's load and never
  looked itself.** That advice has been in the `NO VPP` block since the release
  run that reported `handoff FAIL` over a VPP startup backtrace at load 12 to
  15 on 8 cores, with three agents running labs beside the suite; neither lab's
  source had changed and both passed alone afterwards. It happened again on
  2026-09-15: `detreserve`, `extblocks` and `extblocks+workers` went red on
  *"the record arrived at the collector"*, against collector windows of 25 and
  30 seconds, while an agent ran the whole test suite under `gcov` three times
  over and left three spinning processes on a core for an hour. All three
  passed alone on a quiet machine.

  The suite reads `/proc/loadavg` at the start and at every batch boundary now
  and prints the cpu count and both readings beside the verdict, with one
  sentence - only where there is red to explain - saying that a lab which went
  red on a busy machine is a lab to run again alone. **Not a threshold and not
  a refusal**: the suite's own measurements say `LAB_JOBS=16` peaked at load
  3.09 on 32 cores and was the fastest clean run there is, so a number that
  refused would refuse correct work. What the reader gets is the evidence for
  the question they are about to ask.

  The peak is kept by **value**. `[ "$a" \> "$b" ]` compares strings, so 10.50
  reads as smaller than 9.20 and the peak would be whichever reading had the
  largest first digit - reporting a quiet machine on exactly the busy one this
  is for. The case drives `_load_note()` itself, cut whole out of the suite
  rather than by the line the `awk` is on, because a cut anchored on the
  arithmetic goes red when somebody replaces it and goes red for the wrong
  reason.

* **A value check that fired on what the box already had, and locked every
  commit** ([#358](https://github.com/andrediashexa/hexarouting/issues/358)).
  `set local-preference banana` was accepted at this prompt and committed until
  [#354](https://github.com/andrediashexa/hexarouting/issues/354) gave that
  statement a value check, and FRR stores it and sets nothing - so there are
  boxes carrying one. Without a guard, upgrading to a version that refuses it
  makes **every** commit on such a box fail, and `hexash --load` refuses the
  saved file whole at boot, so a box that was working comes up with nothing on
  it.

  The route-map **reference** check three lines below has carried that guard
  since [#145](https://github.com/andrediashexa/hexarouting/issues/145), with
  the reasoning written out beside it. The value check needed the same guard
  the moment it existed, and it is the second instance of the same shape in an
  hour: [#357](https://github.com/andrediashexa/hexarouting/issues/357) was a
  bound in a reader that also reads the box, and this is a refusal that does
  not ask whether the box already had the thing. **A check that is right about
  a change can be wrong about a fact.**

  Refused only when this change is what causes it. An operator can still fix
  the statement, which turns the finding off; what they cannot be made to do
  is fix it before they are allowed to change anything else. Driven through
  `do_commit` rather than through the finding - the finding is right either
  way, and what this is about is whether the commit is refused.

* **A bound in the reader that also reads the box**
  ([#357](https://github.com/andrediashexa/hexarouting/issues/357)).
  `Config._pool_line()` is the reader for three things: a statement typed at
  the prompt, a saved file, and the configuration read back out of the running
  dataplane. The first is a **request**; the last two are **facts**. #352 put
  the `block-size` bound inside it, and on a box whose pool has a block size
  below 64 - which the dataplane accepts and builds - the VTY could no longer
  model its own running configuration, so `running_config()` said *"committing
  would REMOVE it"* and **`commit` refused every change**. An operator with a
  small block was locked out of configuration on a box that was forwarding
  correctly.

  The bounds are at the prompt now, in `Shell.pool_range()`, which is the only
  place a number can still be argued with. `utilisation-alarm`, `vrf` and
  `outside-vrf` had their only check inside the parser and gain one there; the
  rest were being checked twice. The documented floor of 64 is not the
  dataplane's floor, and the device is the authority on what it has.

  Found by `lab/detreserve.sh`, which builds a four-port block to keep a block
  cheap to fill, and confirmed by running that lab alone - which is what
  separates a defect from a machine that was busy.

  One more thing surfaced while driving it red: `for _name, _typed, _warns in
  LAB:` at module level in `test/test_hexash_complete.py` shadowed `_typed()`,
  the helper that drives the VTY, defined four thousand lines above. Every
  case after that loop which called it got `'list' object is not callable` - a
  crash rather than a red check, so the rest of the file stops and the reader
  gets a traceback instead of a verdict.

* **Seven refusals printed a paragraph on one row**
  ([#355](https://github.com/andrediashexa/hexarouting/issues/355)). `err()`
  wraps nothing, which is right for a verdict and wrong for a paragraph, and
  these were composed as one long sentence: **185, 163, 144, 143, 142, 133 and
  104 columns** against a console of 79. The terminal folded them at whatever
  width the window happened to be, and **in every one of them the move the
  operator has to make is the last clause** - `Remove the sub-interface itself:
  no interface <name>`, `name the subscriber: no forward ...`, `Set the value
  you want instead: ...` - which is exactly where that fold lands.

  `err_prose()` is the split, and it is the one `say_finding` has had since
  [#271](https://github.com/andrediashexa/hexarouting/issues/271) and the
  soft-reconfiguration refusals since
  [#349](https://github.com/andrediashexa/hexarouting/issues/349): the verdict
  through `err`, the rest through `print_prose`, and `failed` raised once
  because it is one refusal however many rows it takes. The `no forward`
  headline also stopped interpolating the subscriber list, which grew without
  bound - the addresses are a prose row of their own now.

  Driven, not read: the case feeds the statements the issue names and measures
  the rows the box printed, and refuses a run that printed nothing or refused
  nothing, because an empty screen fits any console.

* **One configuration came back in two spellings**
  ([#356](https://github.com/andrediashexa/hexarouting/issues/356)). CGNAT has
  lived under `nat` since
  [#193](https://github.com/andrediashexa/hexarouting/issues/193), and
  `Config.text()` put `nat ` in front of the pools and in front of the
  interface attachments and in front of none of the four global settings. So
  an operator reading `show running-config`, `show configuration` or the file
  `save` writes - all three are that one function - found `nat cgnat pool
  residencial` above `cgnat timeout udp 300`. All of them carry the prefix
  now.

  Safe in both directions, and measured: the parser has accepted `nat cgnat
  ...` and the bare form for every global since the hierarchy moved, and
  stores the bare form either way. A file written here loads here, and a file
  written by an older version still loads, which is what `lab/upgrade.sh` puts
  through it.

  One thing found while driving the red: `allg[0]` on an empty list is an
  `IndexError`, and the mutation that removed the prefix killed the suite at
  that line before reaching the check about the spelling. A crash is a third
  state that reads as neither red nor green, so the list is checked for
  content first and the index is a named failure now.

* **Why the range table is a table, measured rather than left as a note.** The
  sweep that fixed the five range defects ended by saying that walking the
  command tree instead of writing the nine rows out would be the obvious next
  step and would need no allowlist. Counted over every upper-case dict the VTY
  exports: **109 range occurrences across 24 help tables, naming 80 distinct
  statements** - so finding them needs no allowlist, and that half is right.

  The rule it would then apply does. `interface <name>` / `vrf` reads
  `<1-4294967295>` in its argument and *"0 takes the interface back out of
  one"* in its description; the box **accepts 0** and clears the statement, and
  its refusal for anything else is `usage: vrf <1-4294967295>, or 0 to take it
  back out`. Three places, one story, and 0 is in two of them. A walk that read
  only the range would call that a defect and send somebody to refuse a value
  the product deliberately takes. The generalisation therefore needs to read
  the description beside the range, or a list of statements to excuse - and a
  check that needs an allowlist is one this repository has already decided not
  to ship.

  Three assertions pin it so the naive repair goes red instead of landing:
  `vrf 0` accepted, the statement actually cleared rather than stored as zero,
  and the refusal above the top of the range still offering 0.

* **The VTY quotes 23 distinct numeric ranges at the operator, and five of them
  were not the ranges the box takes**
  ([#350](https://github.com/andrediashexa/hexarouting/issues/350),
  [#351](https://github.com/andrediashexa/hexarouting/issues/351),
  [#352](https://github.com/andrediashexa/hexarouting/issues/352),
  [#353](https://github.com/andrediashexa/hexarouting/issues/353),
  [#354](https://github.com/andrediashexa/hexarouting/issues/354)).
  [#312](https://github.com/andrediashexa/hexarouting/issues/312) was one site
  of this shape - `commit confirmed` had said `<1-60>` since the day it was
  written, `confirmed 600` armed ten hours and `confirmed 0` armed nothing and
  answered `commit complete`. The other 44 sites were swept the same way, four
  values each - `a-1`, `a`, `b`, `b+1`, and 0 wherever 0 is outside the range -
  through the VTY, and through a live FRR 10.7.1 for the statements this box
  forwards.

  **`vlan <1-4094>` was enforced by nothing.** `vlan 0`, `vlan 4095` and `vlan
  4096` were all staged and committed, and the inner tag had no bound of any
  kind. The sub-interface name check does not bound it: `interface ens19.4095`
  carrying `vlan 4095` satisfies that check, because the name and the tag agree
  and both are wrong.

  **`ARG_NUM = "<1-65535>"` was offered at fifteen statements and is true at
  three.** Two of them contradicted themselves in one sentence - `max-sessions`
  described "0 is unlimited" beside a hint whose bottom is 1. The expensive one
  is `max-blocks`: the dataplane's field is a `u8` in `bpa_pool_t` and in
  `bpa.api`, so `max-blocks 256` was accepted at the prompt, written to the
  file, sent to the device and arrived as **1**.

  **`block-size <64-65536>` was wrong at both ends.** `block_size` is a `u16`
  and `bpa_cli.c` casts to it, so the documented maximum reached the device as
  0 and came back as *"block size 0 must be a power of two"* - naming a number
  the operator never typed. At the other end 32 is a power of two, below the
  documented floor, and was accepted end to end.

  **Four routing hints quoted a range FRR does not have**: `maximum-paths` is
  `(1-256)` in `router ospf` and `router ospf6` and not `<1-64>`; `set weight`
  is `(0-4294967295)`; `set metric` takes negatives and `rtt`; `set-overload-bit
  on-startup` starts at 0. The VTY forwards these and FRR is the authority on
  them - a refusal arriving at `commit` rather than at the line is a different
  experience and not a wrong one - so the text is what moves.

  **`set local-preference <0-4294967295>` is a range FRR does not enforce at
  all**: its grammar is `set local-preference WORD`, and `set local-preference
  banana` is accepted at the prompt, passes `frr-reload.py --test` with exit 0,
  and appears verbatim in bgpd's running configuration, where the clause then
  sets nothing. That one is refused here, in `route_map_findings`, beside the
  `deny` clause carrying a `set` - a policy that looks right and does nothing
  is what that function is for.

  **What this does NOT change, corrected.** An earlier draft of this entry said
  a saved configuration carrying `max-blocks 256` would refuse at boot. It
  does not, and it should not: `hexash --load` reads a FILE, and a value
  already written down is a fact rather than a request. Bounding the reader
  broke a box whose dataplane reported a block size the documented range does
  not cover, and that is
  [#357](https://github.com/andrediashexa/hexarouting/issues/357). The bound
  is at the prompt, where the number can still be argued with; a value that
  reached the device before still reaches it.

  The remaining 18 ranges were driven and hold at both ends. Two that look
  wrong and are not are written down in
  [#353](https://github.com/andrediashexa/hexarouting/issues/353) so nobody
  re-opens them: FRR's own grammar declares `max-lsp-lifetime (350-65535)` and
  then refuses 350 through YANG, and `remote-as <1-4294967295>` matches FRR's
  `ASNUM` token exactly.

  Every bound now has ONE owner - `config.VLAN_MIN`/`VLAN_MAX` and
  `Pool.RANGES` - read by `?`, by the typed parser and by the file parser, so a
  hint and the check behind it cannot be two numbers that drift apart.
  `test_hexash_complete.py` drives all four values at every range this VTY
  enforces and requires the refusal to NAME the range, which is the half that
  tells a range refusal from a refusal that happened to fire for some other
  reason - the distinction `confirmed 0` turned on.

* **The vector table's coverage of both mapping policies rested on nobody
  removing a pool.** `bpa_split()`, `bpa_join()` and `bpa_reserve_index()` each
  have an arm for the blocked policy that nothing in this repository reaches
  without a container, and `bpa_divide()` has a branch for a divisor that is
  not a power of two. A coverage report suggested all four were unexecuted
  offline. **They are not**: measured on the table as it stands, three pools
  are blocked - `frag-blocked`, `adjacent` and `reserved-blocked`, with
  divisors 126, 31 and 126 - and `test/test_bpa_pool.c` walks every pool, so
  the harness now prints `3 blocked and 6 interleaved pool(s); 3 divisor(s) a
  power of two and 6 not`.

  What was missing is anything that would notice them going. A coverage report
  is read by whoever runs it; this goes red in `make test` the day somebody
  drops the last blocked pool from `ref/gen_vectors.py`, which would otherwise
  leave the reverse mapping that decides **which subscriber an inbound packet
  belongs to** measured under one policy only. Both halves of `bpa_divide()`
  are held the same way, because `ref/check_magic.py` proves the reciprocal
  multiply exact and that proof is about a branch nothing would be taking.

* **Three numbers stated in the present tense inside the product, in comments
  beside the code that computes them.** `test/test_release_gate.sh` said *"the
  gate refuses eighteen times"* on the line directly above
  `REFUSALS=$(grep -c 'echo "error:' ...)`, and the gate refuses **24** now. The
  header of `tools/plugin-coverage.sh` and the comment at
  `test/test_bpa_dyn.c:86` both said the C harnesses *"run 65,376 checks"*;
  summing what those ten harnesses print gives **65,767**, and it grows every
  time a case is added. Found while sweeping `## Unreleased` for the same shape
  - a sentence true when it was written and false now - which turned up eleven
  there and these three in the code beside it.

  None of the three is replaced with a fresher number. The gate's comment
  points at the count three lines below it, and the other two say where the
  number comes from and record what it was, because a count in prose beside a
  count in code is the second copy this repository keeps paying for and a
  bigger number would only reset the clock on it.

* **The two screens that refuse a soft re-read printed eight lines wider than
  the console** ([#349](https://github.com/andrediashexa/hexarouting/issues/349)).
  `err()` wraps nothing and the rows under those refusals were bare
  `print("    ...")` calls, so they measured **106, 101, 102, 92, 105 and 81**
  columns against 79 - and the two "FRR is not answering" headlines written in
  the same cycle measured 86 and 99. The neighbour address is interpolated into
  every one of them, so an IPv6 peer is wider again, which is exactly the case
  [#276](https://github.com/andrediashexa/hexarouting/issues/276) widened a
  check to cover on a different message. These rows were not among the ones it
  split and nothing read their width. They go through `print_prose` now and the
  headlines carry only the refusal.

  Driven over three peers - one IPv4 and two IPv6 - on all four paths, and the
  case refuses a run that printed nothing, because an empty screen passes a
  width test for free.

* **The installer sent the operator to a path the box does not have**
  ([#348](https://github.com/andrediashexa/hexarouting/issues/348)). The
  connection-records question said *"one year at 200k subscribers in dynamic
  mode is about 29 GB (see `docs/logging.md`)"*. There is no `docs/` on a box
  this product installs: `appliance/install.sh` puts that file at
  `/usr/share/doc/hexarouting/logging.md`, and the person answering that
  question has the box rather than the repository. The public installation page
  quotes that screen verbatim - because the rule there is that box output is
  what the box prints - so the path nobody could follow reached a reader who
  had not installed anything yet. Both now name the path the box has.

  `test/test_appliance_paths.py` holds it, with two rules and no allowlist
  behind either. **No script that runs on a box prints a repository-relative
  path**, where repository-relative means the first component is one of this
  tree's own top-level directories - the rule `test/doc_paths.py` established
  and measured. **And a document named under `/usr/share/doc/hexarouting/` is
  one `install.sh` puts there**, read out of `install.sh`'s own loop so that
  adding or dropping a document moves both sides at once. Which scripts run on
  a box is `install.sh`'s answer too: a file it copies into `$DESTDIR` is a
  file that runs where `docs/` does not exist, so `build-iso.sh` and
  `install.sh` themselves - which run in a checkout, by somebody who has the
  repository - are outside the rule without being listed as exceptions.

* **`show hardware`'s Link and Speed columns ran together**
  ([#347](https://github.com/andrediashexa/hexarouting/issues/347)). The Link
  column was seven wide and `unknown` is seven characters, so a card in that
  state printed `unknown-` and `unknown1000Mb/s` with no space at all, on the
  screen an operator picks a card from. `unknown` is not an odd value:
  `host_nics()` falls back to it when `operstate` cannot be read, and the
  kernel writes it for any netdev whose driver sets no carrier - which is every
  card on a box nobody has tuned yet. The column is 15 now, which is
  `lowerlayerdown` plus a space, sized from the closed set RFC 2863 defines
  rather than from the states this box happens to have: `notpresent` is ten and
  would have pushed Speed out again. The width is one name used by the header
  and the rows, so the two cannot drift.

  Driven over all seven states, and what is asserted is where the **speed**
  starts rather than how wide the state is - a row whose speed begins at a
  different offset from the header's is a table nobody can read down. At the
  old width three states run together; at eight, which is enough for
  `unknown`, two still do and the speed starts in three different columns.

* **The one-card handover printed two lines wider than the console it is read
  on** ([#346](https://github.com/andrediashexa/hexarouting/issues/346)). It is
  the path a VM operator meets first, and `config.CONSOLE_COLUMNS` is 79. The
  headline was **81 columns** on `ens18` and would be 91 on the longest name
  Linux can carry, because it was printed with a bare `print()` rather than
  through `print_prose` and the name is interpolated into it. The completion
  row offered **before** the name is typed - the row that tells the operator
  what handing this card over means - was **100**. The two-card spelling of
  the same entry has always been 44.

  The real fix is the third one: the width sweep in `test/test_dataplane_if.py`
  collected from the two-card box only, because `one_card_box()` is defined
  three hundred lines below it. So the one path where the box gives away the
  card the session is on was the one path nobody measured. It is measured now,
  with the longest name Linux can carry and with the completion rows rendered
  the way the VTY renders them, and the case refuses a run in which the box did
  not accept the name - or the widest line it measured would be a refusal and
  not the handover.

* **Twenty-one corrections on eight public wiki pages, and the rule about that
  wiki had no reader.** Swept against the product since the last wiki sweep of
  2026-09-13, which is three releases and a day of defect work ago - most of it
  about the box with **one** network card, which the pages were written before
  this product could bring up. Every correction measured by running the thing
  that prints it.

  **The rule that had nothing reading it first.**
  `PADRAO-DOCUMENTACAO.md` section 0 says an issue number, a GitHub link, a
  source file name and the dataplane's own text CLI never appear on a public
  page, and gives a reason for each. Four pages named `vppctl`: two offering
  it beside a `show` command, one as a diagnostic trap worth knowing, one in
  the architecture sketch. None was added carelessly and all four read as
  helpful, which is the difficulty - a rule with no reader erodes one
  reasonable-looking sentence at a time. `test/test_wiki_rules.py` reads all
  32 published pages for the three needles, and is shown red on each: a page
  naming the CLI, a page citing `#303` and github.com, a page naming
  `src/hexash/hexash.py`, and the wiki moved out from under it so the three
  "this list is empty" checks cannot pass by having read nothing.

  WHOLE PAGES, fences included, and the exemption that was there first is
  gone. The argument for exempting quoted box output is real -
  `appliance/hexarouting-install` prints "see docs/logging.md" at the
  connection-records question, which is the installer's defect and not the
  page's - but measured over all 32 pages nothing goes red with the fences
  read, because that line is not backticked and a backtick is what
  `doc_paths.repo_paths` looks for. The check written to prove the exemption
  worked could not fail either: its own sample had no backticks in it. An
  exemption protecting a case that does not exist, with a vacuous guard under
  it, is two defects.

  **What the pages said and the box does.** Each measured by driving the
  product, not by reading it: the VTY through the seams
  `test/test_dataplane_if.py` uses, the self-test against a fake root the way
  `test/test_selftest.sh` arranges one, the tuner against a synthetic virtio
  guest, and `appliance/bind-dataplane-nic` on the no-IOMMU box in
  `test/test_bind_nic.sh`.

  *Operação e diagnóstico* opened with "the management interface does not go
  to VPP", flat, as the first rule on the page. That is the rule wherever
  there is another card to name, and since
  [#303](https://github.com/andrediashexa/hexarouting/issues/303) the one-card
  box is the exception the product has an answer for - the same correction
  [#343](https://github.com/andrediashexa/hexarouting/issues/343) made to
  `docs/context.md`'s invariant 1.5, in the place a customer reads it.

  **A fourth change becomes a confirmed commit on its own, and the pages knew
  three.** `commit` on a handover arms `commit confirmed 30` and its revert
  restarts the box; the commit reference and the operations page both listed
  ssh, user and cutoff at ten and stopped. The thirty, the reason for it - a
  reboot and a login rather than a reconnection - and the restart are on both
  pages now, with the two screens as they print.

  **The self-test reports the tuning in EFFECT**
  ([#163](https://github.com/andrediashexa/hexarouting/issues/163)), and the
  requirements page still said it compares the file against the booted command
  line. The four states are on the operations page with the lines the box
  prints, driven against a fake root whose kernel refused `isolcpus` and one
  that exposes no `nohz_full` readback.

  **The tuner reads the card**
  ([#330](https://github.com/andrediashexa/hexarouting/issues/330)) and the
  wiki said nothing at all - on the page somebody reads before buying, about
  the machine most of them run. All four report lines measured on synthetic
  guests: capped, enough, could-not-be-read, and the MTU below 9000.

  **Four quoted blocks were not what the box prints.** The `?` listing still
  offered `lo`, which stopped being offered when completion started refusing
  to name a card the box would then refuse; `it has:` is `the cards it has:`;
  `show hardware` on a box with no card still said to edit
  `HEXA_DATAPLANE_IF` in the tuner's file and restart two units, which is the
  advice [#283](https://github.com/andrediashexa/hexarouting/issues/283)
  replaced with three statements in this VTY; and the expiry transcript had
  the first two lines and the last, with the four between them - the ones
  saying what became of the setting - dropped without a mark.

  **And five blocks were folded by hand.** The one-card handover headline, the
  no-IOMMU bind line and the two soft-reconfiguration refusals are printed by
  the box as single long lines, and the pages had them wrapped at 79 as though
  the box had done it. They are quoted as printed now, which is also how the
  next three entries were found.

  **Three product defects, filed rather than papered over.** The one-card
  path prints an 81-column headline and a 100-column completion row, and
  `bind-dataplane-nic` a 121-column line, on a console this product holds at
  79 everywhere else - the width sweep in `test/test_dataplane_if.py` collects
  its lines from the two-card box only
  ([#346](https://github.com/andrediashexa/hexarouting/issues/346)).
  `show hardware`'s `Link` column is seven wide and `unknown` is seven
  characters, so a card with no driver bound prints `unknown-` with no space
  before the speed
  ([#347](https://github.com/andrediashexa/hexarouting/issues/347)). The
  installer sends the operator to `docs/logging.md`, which is a path in this
  tree and not on any box it installs
  ([#348](https://github.com/andrediashexa/hexarouting/issues/348)). And the
  two soft-reconfiguration refusals print six rows between 81 and 106 columns,
  each carrying a statement the operator is meant to type back
  ([#349](https://github.com/andrediashexa/hexarouting/issues/349)).

* **The 86 lines of the BPA plugin no docker-free check had ever executed, and
  the measurement that could not see them.** `tools/plugin-coverage.sh` reports
  which lines of `src/plugins/bpa/` the ten C harnesses reach. On master it said
  108 of 4807 executable lines were never executed. Both numbers were wrong, in
  opposite directions, and for two reasons in the tool rather than in the
  plugin:

  * **It reads one harness's counters per file and reports them as the whole.**
    `gcov -o .build <gcda>` writes `bpa_pool.h.gcov` into the repository root,
    and the loop runs it once per object - so for every source built into more
    than one harness the last object's file overwrites all the others.
    `bpa_pool.c` is compiled into `test_bpa` AND `test_bpa_dyn`, and
    `test_bpa_dyn` sorts last, so the reported coverage of `bpa_pool.c` was
    `test_bpa_dyn`'s alone. It listed all fourteen of `bpa_addr_set_build()`
    and `bpa_pool_init()`'s refusals as never executed. Ten of them are driven
    by `test_bpa_pool.c`, and have been for as long as that file has existed.
  * **It counts gcov ROWS, not source lines.** gcov emits one row per inlined
    instance of a line in a header, and suffixes a count with `*` when some
    instance did not run it. `grep -cE '^ *#####:'` counts a line as never
    executed when ANY instance did not, and `grep -cE '^ *[0-9#-]+:'` counts
    the same source line once per instance. A plugin whose headers are almost
    entirely `static_always_inline` is nothing but such lines, which is why
    `bpa_pool.h` was reported with 1251 executable lines in a 1248-line file.

  Merged across every object, with each line counted once and `*` read as
  "executed", the honest baseline was **1101 of 1187 executable lines reached,
  86 never**. It is now **1184 of 1194, 10 never**, and all ten are named
  below. The denominator moves because an unused `static_always_inline` is not
  emitted at `-O0` at all, so its lines are in no counter: a function nothing
  calls does not appear as uncovered, it does not appear. The baseline
  therefore understated the gap as well as misplacing it.

  The subject, counted by reading rather than by grepping: **590 refusal sites
  across `src/plugins/bpa/`**, of which **172 are in the fourteen files
  `make test` compiles**:

      bpa_sip.h  41   bpa_dyn.c  25   bpa_pool.c 14   bpa_ports.h  8
      bpa_pool.h 28   bpa_rtsp.h 21   bpa_icmp.h 10   bpa_dyn.h    6
      bpa_mss.h   6   bpa_rewrite.h 4 bpa_seq.h   4   bpa_auto_mss.h 3
      bpa_gre.h   1   bpa_ftp.h   1

  and **418 in the thirteen files it does not**:

      bpa_cli.c  111  bpa.c      80   bpa_ftp.c  40   bpa_session.c 27
      bpa_in2out.c 26 bpa_log_tcp.c 24 bpa_out2in.c 23 bpa_pptp.c   23
      bpa_audit.c 20  bpa_api.c  19   bpa_log.c  17   bpa_handoff.c 8
      bpa_ra.c    0

  `bpa_ra.c` holds zero, and structurally: it re-registers ICMPv6 type 134 with
  `ip6-punt` as its only next node, which is what makes "no refusal" a property
  of the registration rather than an omission.

  **The allocator, which is where a refusal costs a subscriber something.**

  `bpa_dyn_acquire()` had been called 864,834 times by this repository's C
  harnesses and had NEVER found the subscriber already holding a block: every
  scenario acquired once per subscriber and then extended, so the three lines
  behind `q != 0` - the path every packet after a subscriber's first takes -
  were never executed. Wrong, they hand a second block to the second flow,
  which is one customer on two public port ranges and a pool that empties at
  the rate flows arrive; and they emit a second RFC 8158 natEvent 16 with no
  17 between them, which is the gap `docs/logging.md` exists to close. Shown
  red by looking the subscriber up under `sub_idx + 1`:

  > *"FAIL: the second flow left on public address index 1 and the first on 0:
  > one subscriber, two public addresses"*

  and the hold-down half, shown red by deleting `d->idle_since[local] = 0`:

  > *"FAIL: the subscriber came back and its block is still counting down from
  > 100.0"*
  > *"FAIL: the block of a subscriber that came back 900 seconds ago was reaped
  > anyway"*

  **What the reap will not take.** Two of its three skips had never been
  executed. A pinned block is the operator's static forward, and the state it
  is in every morning is "idle for longer than the hold-down"; a block still
  inside its hold-down belongs to a subscriber that may come back. The three
  are driven in one pass with a block that MUST be released beside them, so a
  reap that refused everything is as red as one that refused nothing:

  > *"FAIL: the pinned block was reaped 990 seconds into a 120 second
  > hold-down, so the operator's forward now points at whoever takes it next"*
  > *"FAIL: the block of a subscriber 100 seconds into a 120 second hold-down
  > was released"*

  **The whole of `bpa_dyn_pin()`'s allocate arm and `bpa_dyn_unpin()`'s release
  arm** - eighteen lines - had never been executed. `*is_new` and `*released`
  are what become natEvent 16 and 17:

  > *"FAIL: the block came off the free stack and the caller was not told to
  > emit a natEvent 16, so nothing records that it left the pool"*
  > *"FAIL: re-applying the same forward reported a second allocation, which is
  > a natEvent 16 with no release between it and the first"*
  > *"FAIL: the pin was the only thing holding the block and the caller was not
  > told to emit a natEvent 17, so the audit trail still says subscriber 7 has
  > it"*
  > *"FAIL: 1 entries are left on the extras list naming a block that is back
  > in the pool, so the next subscriber to be given it has its block stamped
  > idle by somebody else"*

  **And the refusals around them**: a pin aimed at a block outside this
  worker's slice (`-2`, distinct from `-1` and going to a different place), a
  dynamic slice the capacity does not reach, a reserve slice that comes up
  EMPTY rather than refusing - both arms of the one `if` in `bpa_dyn_init()`,
  so a test of the dynamic half alone stays green with the reserve half
  deleted - and the three lines where the reserve's paired-address walk gives
  up. That last one needed three workers over four public addresses and the
  subscribers of one address arriving together: driven in index order every
  address runs out of reserve blocks at the moment the whole slice does, and a
  worker whose free stack is empty refuses before the walk is entered at all.
  10 of 32 reserve extensions pair there, 22 cannot, and none of the 22 is
  refused for it.

  **The mapping, where a refusal decides which subscriber a packet belongs
  to.** `bpa_reverse_sub()` answers for a mapped port and
  `bpa_reserve_index_of()` for a reserve one, and between them they must
  partition the port range on every public address; three of the four refusals
  that make that partition had never been executed. Walked block by block over
  a whole address rather than sampled, with both answers counted:

  > *"FAIL: 123 ports were claimed by both directions and 0 by neither"*
  > *"FAIL: a public address outside the pool resolved to reserve index 0"*
  > *"FAIL: reserve index 21 was resolved on a reserve of 21 blocks"*

  **The parsers.** `bpa_sip.h`'s 23 never-executed lines are now zero: the
  refusals of `bpa_sip_hostport()`, `bpa_sip_via()` and `bpa_sip_contact()`,
  the bound on how many spans one message may contribute, and two ACCEPTING
  paths that fail silently - a `sips:` Contact and an SDP `c=` line written
  with the whitespace RFC 4566 allows. Both of those leave a subscriber
  translated and unreachable:

  > *"FAIL: a sips: Contact and a spaced-out c= line refused the message"*
  > *"FAIL: the media port was not governed by the spaced c= line: 8000 at
  > 00000000"*
  > *"FAIL: a message whose an SDP m= span is the 15th was not refused whole"*

  `bpa_icmp.h`'s `default:` arm - an embedded protocol this box has no key for
  - is the refusal #203 is a record of, and nothing had driven it. Four
  protocols, chosen for what they would be mistaken for: SCTP and DCCP carry
  ports where TCP does, AH sits beside ESP, and IPv4-in-IPv4 quotes a version
  and a length. The verdict structure is compared as a whole against a poisoned
  copy, so a branch that filled in a key before deciding it could not use one
  is red too:

  > *"FAIL an ICMP error about embedded IP protocol 132 was accepted, and there
  > is nothing in it this box keys a session on"*
  > *"FAIL the refusal of protocol 132 left a key behind: address 3365273617
  > port 1536 call id 0 protocol 132"*

  `bpa_rtsp.h`: the reply start line, where the parser decides whether the
  bytes in front of it are RTSP at all, and the whitespace an encoder may put
  between a header name and its colon. Every reply fixture carries a
  well-formed `Transport` header so the START LINE is the only thing deciding
  the verdict.

  **The ten lines still never executed, each with the argument that they
  cannot be**, rather than left as a number:

  * `bpa_dyn.c:160` and `:405` - `bpa_dyn_addr()` failing inside acquire.
    `block_idx` is `local * n_workers + worker` with `local < n_local`, and
    `n_local` counts the indices congruent to this worker below the total, so
    `block_idx` is always below `reserve_count`.
  * `bpa_dyn.c:457` - `bpa_dyn_unfree()` on a block not on the stack.
    Unreachable while `free_pos[]` and `free_list[]` agree, which
    `index_disagreements()` asserts after every path that moves them.
  * `bpa_dyn.c:610` - `bpa_dyn_reap()` on an inactive slice. `bpa_dyn_free()`
    memsets, so an inactive slice has `n_local` 0 and the loop body never runs:
    the function returns 0 with the guard and without it, and no assertion can
    separate them.
  * `bpa_pool.c:229` - "divisor is zero". Stands behind "address sets must be
    built first" under `policy interleaved` and is unreachable under `policy
    blocked`, where the reserve refusal guarantees `blocks_mapped >= 1`.
  * `bpa_pool.h:763`, `:774` - the multi-range search in `bpa_set_addr_of()`
    running out. `bpa_addr_set_build()` assigns `first_idx` cumulatively from
    zero, so every index below `count` is inside exactly one range.
  * `bpa_pool.h:990` and `:1178` - `bpa_set_addr_of()` failing inside
    `bpa_reserve_addr()` and `bpa_forward()`. Both callers have already bounded
    the index they pass.
  * `bpa_ports.h:106` - "n_free disagreed with the bitmap", which is the
    invariant being broken rather than an input.

  **Two refusals whose evidence is a mutation nobody can write**, recorded
  because a check that cannot go red is worse than no check:

  * `bpa_pool.c:155`, a block wider than the port range it is cut from. Such a
    pool also has `block_reserve >= blocks_per_ip` - zero is not less than zero
    - so the reserve refusal three lines below refuses the same configuration.
    The difference between them is the MESSAGE, and `test/bpa_shim.h` keeps
    none: VPP's `%v` is not a printf specifier, so the shim cannot hand the
    caller's format to vsnprintf and every `clib_error_return` under
    `BPA_STANDALONE` is one object. The configuration is pinned; the wording
    that says which field is wrong is asserted nowhere docker-free.
  * `bpa_reserve_index_of()`'s port-range test. Below `port_min` the unsigned
    subtraction wraps to a block index of about 2^26 and above `port_max` it
    lands exactly on `blocks_per_ip`, so the reserve-block test below refuses
    both on its own. It is a bound that keeps the shift well defined rather
    than a behaviour, and it is stated as such.

  Three more are backstopped the same way and are noted where they sit:
  `bpa_rtsp.h`'s "the second port has no digits" (the consecutive-pair test
  refuses it anyway), and `bpa_sip.h`'s unterminated IPv6 reference and its
  comma-in-a-display-name (the span-exactness test refuses both).

  **`bpa_rtsp.h`'s `len < 12` bound has no falsifiable case in that harness at
  all**: no eleven-byte prefix gets past the version and status checks below
  it, so there is no payload for which removing the bound changes an answer. It
  is a memory-safety bound, and showing one takes the guard page
  `test/test_bpa_icmp.c` builds. Named rather than tested.

  **A mutation that hangs is a third outcome.** Weakening `bpa_set_addr_of()`'s
  `idx >= s->count` to `idx > s->count` does not fail `test_bpa`, it hangs it:
  the search below opens with `hi = s->n_ranges - 1`, so on a set nothing was
  built into that subtraction wraps to `0xffffffff` and the binary search walks
  two billion entries past the end of a 256-entry array. The bound is therefore
  load-bearing for memory safety and not only for the answer, and the mutation
  that shows the check neutralises the ANSWER instead. No input can reach it
  through the product - `bpa_pool_init()` refuses an empty set - but
  `bpa_set_addr_of()` is `static_always_inline` and takes the set it is handed.

  **What the 418 need, and what they do not.** A census of all thirteen says
  **171 of them could be driven with no container at all** - they are
  arithmetic over `bpa_pool_t`, `bpa_static_t`, a `u8 *` payload or a socket,
  with no `vlib_main_t`, no `vlib_buffer_t` and no feature arc - and none of
  the thirteen files is compiled by `make test` today. That is a count from
  reading, not from a run, and the three worth naming:

  * **`bpa_ftp.c`: 31 of its 40**, the whole of `bpa_ftp_dec`,
    `bpa_ftp_starts`, `bpa_ftp_parse_hostport`, `bpa_ftp_parse_dotted` and
    `bpa_ftp_parse`, which take `(const u8 *, u32, ...)` and nothing else. The
    RFC 2428 delimiter rules, the "port zero is not a data connection" refusal
    and the CRLF-not-bare-LF rule are all in there. `test/test_bpa_ftp.c`
    already exists and covers `bpa_ftp.h`'s one standalone function and
    `bpa_seq.h`; reaching the parser needs the file split the way `bpa_ftp.h`
    already is, at `bpa_ftp_in2out`, which is the only part that touches a
    buffer.
  * **`bpa_log_tcp.c`: 23 of its 24.** No `vlib_main_t`, no buffer, no
    unformat, no vnet anywhere in 568 lines - the clock arrives as an `f64`
    parameter. The spill file, the framing walk and the reconnect timer are
    all drivable with a temporary directory and a loopback listener. The one
    exception is `bpa_log_tcp_enable`, which calls `bpa_log_pins_replay()` and
    so takes the worker barrier.
  * **`bpa_session.c`: all 27**, including every refusal of
    `bpa_alg_reserve_pair` - the even-aligned media pair an RTSP or SIP call
    needs, which is the one whose failure is a connected call with no audio.
    They take `bpa_per_thread_t *`, `bpa_pool_t *` and an `f64`, and need
    clib's pools, vectors and bihash rather than the graph.

  `bpa_api.c`'s 19 and `bpa_cli.c`'s 95 of 111 genuinely need the runtime, and
  for different reasons: the API handlers all end in `REPLY_MACRO`, which needs
  a client registration; the CLI's parse refusals DECIDE over vppinfra and
  speak through `vlib_cli_output`, so what needs VPP is the sentence and not
  the judgement.

  `make test`: 65 suites, 65 passed.

  Four entries in this section end on a line like that one, at 59, 61, 63 and
  65, and none of them disagrees with another: each is the run that cleared
  its own merge, and the count is what `test/suites.list` named that hour.
  `test/suites.list` names **69** today. A reader who takes any of the four
  for the size of this repository's offline suite is reading a timestamp as a
  measurement.

* **The bind failure's advice is false of both boxes that can reach it**, in
  opposite directions
  ([#346](https://github.com/andrediashexa/hexarouting/issues/346) for the
  width half). `appliance/bind-dataplane-nic` ended every failed bind with one
  sentence for both drivers: *"on a box with an IOMMU the usual cause is
  `intel_iommu=on` or `amd_iommu=on` missing from the kernel command line"*.
  With `vfio-pci` the box **has** IOMMU groups - that is the only way
  `dpdk_driver()` returns it - so the parameter cannot be what is missing, and
  an operator whose appliance came up not forwarding was sent to check a
  kernel command line that is already right, on the one message they get. With
  `uio_pci_generic` the box has **no** IOMMU, so a sentence opening "on a box
  with an IOMMU" is about a different machine and names a parameter that would
  not have helped, because that driver does not use an IOMMU at all. Each box
  is now told something true of itself: the one with groups is pointed at
  `/sys/bus/pci/devices/<addr>/iommu_group`, and the one without is told that
  `uio_pci_generic` needs a legacy INTx interrupt and what would make
  `vfio-pci` available instead.

  The fallback announcement was also **121 columns** on an 80-column console,
  which is the widest line this appliance prints and it is on the path a
  one-card VM takes. `say` joins its arguments with a space onto one line, so
  a continuation in the source is not a line break in the output. Folded, and
  the whole of that run's output is now held under 79.

* **The release gate refused without saying so, and its last word was a green
  check.** The three document comparisons - what the published wiki says, what
  `docs/roadmap.md` says, and what the published release is titled - print
  their refusals on stderr and their passes on stdout, and the gate then ran
  `[ "$docs_bad" = 0 ] || exit 1`. A refused run read through its stdout alone,
  which is a log or a pipe or `| tail -1`, ended on **`==> the published
  release title is the CHANGELOG heading`**: a green line under a release that
  was refused. Every other verdict in this repository says how many and what it
  means, and this one said nothing at all.

  It is a **count** now rather than a flag, because the flag could not tell one
  red check from three: each of the three set `docs_bad=1`, so a run that
  failed all of them and a run that failed one exited identically. The closing
  line names the number and the three checks it is about.

* **The tuner report `docs/appliance.md` quotes is from a box the page never
  names, and one of its numbers does not follow from the property it does
  name.** The page prints that block under "A box configured for 20,000
  sub-interfaces". The statistics segment does follow from that: 20,000 x
  4,793 bytes is 92M and moves with nothing else. **The hugepage figure does
  not.** It is the session table for the subscriber target plus one gigabyte
  per four workers, so the worker count is the box: the same 200,000
  subscribers ask for **6 GB** on the one-socket eight-core machine the page
  quotes and **12 GB** on a 32-core one, measured by running the tuner on
  both. An operator who ran it on their own hardware and read a different
  number had nothing on the page to reconcile it with. The page names the box
  now and says which of the two numbers moves with what.

  `appliance/test_tune.py` already builds exactly that box, so it now requires
  **every line of the quoted report to be a line that run printed** - the
  [#326](https://github.com/andrediashexa/hexarouting/issues/326) rule one step
  further, where a page quoted a tuner line the tuner had stopped printing and
  nothing compared them. One substitution is made before comparing and it is
  not a loosening: the report names the file it counted interfaces in, which is
  `/etc/hexarouting/hexarouting.conf` on a real box and a temporary directory
  in the fixture. Comparing without it reported the page wrong about a line
  that is right, which is a check going red on correct work.

* **The lab suite needs the network and nothing said so.** `TESTING.md`'s
  three levels read as no network, then a container, then real Internet, and
  level 3 is the one whose heading says so - which leaves level 2 reading as
  not needing it. `lab/bng.sh` installs five `-dev` packages with `apt-get` and
  clones `accel-ppp-ng` from GitHub on **every** run, because the container is
  `--rm` and nothing caches. On a box with no route out it goes red and takes
  the whole suite with it, and a release is then refused over a file
  [#170](https://github.com/andrediashexa/hexarouting/issues/170) calls a
  spike. The suite now says it before it starts, from a list held beside the
  one that names the labs it is not running at all, so a second lab that grows
  a network dependency is named by adding a line rather than by somebody
  remembering. Found while sweeping the last documents nobody had read.

* **Two documents describe the one-card box as the box that does not exist**
  ([#343](https://github.com/andrediashexa/hexarouting/issues/343),
  [#344](https://github.com/andrediashexa/hexarouting/issues/344), both from
  [#303](https://github.com/andrediashexa/hexarouting/issues/303)). Same shape
  as the five before them: a sentence that is true of one box and false of
  another, written by a path that never asked which box it was on.

  **`docs/context.md` invariant 1.5** said the management interface **MUST
  NOT** be given to VPP and that unreachable is unfixable remotely. Since #303
  the box with one card is the one case where it may go: the VTY accepts it,
  writes the management path down, carries the address onto the `linux_cp`
  pair with the MAC, and applies the commit as `commit confirmed 30` so that a
  handover nobody confirms undoes itself and restarts the box. The page 1.5
  cites as authoritative, `docs/appliance.md`, says exactly that. The invariant
  now states the rule, where it still binds - every one of the three places
  refuses the default-route card wherever there is another to name - and the
  one exception.

  **`docs/appliance.md`'s post-install procedure** is `configure`,
  `dataplane interface <name>`, `commit`, edit `tune.env`, reboot. Measured:
  on a two-card box that is a plain commit and the procedure is complete. On a
  one-card box the same three statements arm `commit confirmed 30`, a reboot
  confirms nothing - the deadline is absolute and lives on disk - and the box
  puts `HEXA_DATAPLANE_IF` back and restarts itself. An operator who followed
  the page ends where they started, half an hour later, with nothing they did
  to explain it.

  **One holder, and the box goes first.** `test/test_dataplane_if.py` drives
  both boxes through the three statements the page prints, reads the window
  off the box that armed one, and requires the pages to name that number and
  the step after the reboot. A check that read the pages for the word
  "confirm" without watching a box arm a window would stay green on the day
  the VTY stops arming it, which is the day the pages become right by
  accident. Shown red at `takes_management = False`, at
  `takes_management = True`, at `confirm_min = 20` with the pages still saying
  30, and with either sentence deleted from either page.

* **The self-test's rx-queues line was the request, and read like a
  measurement** ([#345](https://github.com/andrediashexa/hexarouting/issues/345)).
  `[ ok ] rx queues: num-rx-queues 7` comes from `/etc/vpp/startup.conf`, which
  is what the tuner asked the dataplane for. A card with fewer queues gets what
  it has, and
  [#330](https://github.com/andrediashexa/hexarouting/issues/330) is the box
  where VPP refused the interface outright over it: seven asked for on a virtio
  NIC with one, the box booted with no dataplane at all, and this line printed
  green about seven queues. It says `rx queues requested:` now. The cores three
  sections below are read back off the running process and say so; this one
  cannot be yet, and the reason is in #345: `show hardware-interfaces` prints
  `rx: queues N (max M)` only for a DPDK interface, and prints nothing of the
  kind for the packet-generator interfaces every lab here uses - measured in
  the dev container, where `pg0` gives a name, a link state and a MAC. A reader
  written against a format nobody on this box has seen, tested against a
  fixture that fabricates it, would prove nothing.

* **The soak's collector stopped before the box did, and the records it missed
  read as a leak**
  ([#342](https://github.com/andrediashexa/hexarouting/issues/342), follows
  [#30](https://github.com/andrediashexa/hexarouting/issues/30)). The
  eight-hour run of 2026-09-15 ended `LAB FAILED: 2 check(s)`, both about the
  far side of the log, and both caused by the lab. The box's own counters read
  **104,227 allocations against 104,227 releases with zero blocks in use**, and
  `stop-drain` reported no record dropped; the collector read 104,227 against
  **104,181**, with 46 open. Those 46 are release records that arrived after it
  had stopped listening. It was given `CHURN + 40` seconds fixed before the
  run, and 822 cycles of packet-generator and `vppctl` calls drift about 80
  seconds past 822 x 35s, so the budget expired inside the final cycle: it
  heard that cycle's allocations and none of its releases. The 46 carry two
  timestamps between them, one burst, and the exporter's boot marker sits
  exactly eight hours and nineteen seconds earlier.

  A collector outlived by its exporter reports what it did not wait for as
  allocations nobody ever released, which in the log an operator is obliged to
  keep is indistinguishable from a leak. The assertions were right; what was
  missing was anybody saying the far side had been given the chance to hear
  the end. `tools/ipfix_collector.py` now takes a stop file on the UDP path as
  the TCP path already does for its stall, drains what is already in the
  receive buffer before leaving, and prints **`STOPPED BY:`** - the caller, or
  its own budget. `lab/soak.sh` keeps a budget only as a ceiling, scaled with
  the run because the drift is, and closes the far side itself once the box's
  own blocks in use reach zero. Two checks now come before the two that were
  red, because they are what makes those mean anything: that the box had given
  every block back, and that the collector was stopped by the lab rather than
  by its own budget.

* **A floor below what the off run measures was silent, which is how floors go
  stale** ([#151](https://github.com/andrediashexa/hexarouting/issues/151)).
  `lab/ipfix-outbuf.sh` declared `OFF-RED-AT-LEAST: 8` and its off run went
  red on nine, on 2026-09-14 and again on 2026-09-15. The ninth is the check
  repaired on 2026-09-13: it had been true for every value, so it was green in
  the off run too and was never among the eight, and the commit that repaired
  it wrote "the floor is unchanged by this and was not re-measured" into the
  header. Nothing ever asked the off run whether that was still true, so for
  two days deleting that assertion again would have cost nothing. The floor is
  now 9 and its header names all nine by their words. The suite says so from
  now on: a pass whose red count is above its floor prints how far behind the
  floor is and what raising it would buy - not a failure, because a lab that
  grew is the good direction, but not silence either.

  The case that was supposed to hold the ordinary reading of that line - a lab
  red on exactly its floor - could not fail. It was written on `urpf+off`,
  whose stub capture opens with `VPP did not start`, so the report classifies
  it NO VPP three branches before any floor is read, and the stub lab was
  given no floor to read either. The case accepted `NO VPP` as a second answer
  and said in its own words that this was what a case forty lines above
  already asserted. Put back on its own, without that escape, it is red: it
  never matched anything. A lab landing exactly on its floor now has a stub of
  its own.

* **The only assertion in the tree about the `match` pipe filter was the
  filter's own guard quoted back at it** (`test/test_hexash_complete.py:2582`).
  `apply_pipe()` in `src/hexash/hexash.py` routes both spellings through one
  branch,

  ```python
  if verb in ("include", "match"):
      lines = [l for l in lines if arg in l]
  ```

  and the check under it read `check(piped("match mtu") == piped("include
  mtu"))`. That is one expression compared with itself: whatever the branch
  computes it computes for both verbs, so nothing the filter does can separate
  them. It is the Python instance of the shape `test/test_c_restates_product.py`
  refuses in the C harnesses and `test/test_lab_vacuous.py` refuses in the labs,
  and `match` is piped nowhere else in the tree, so this line was the whole of
  its coverage.

  The mutation has to be the SHARED branch and cannot be either verb's name:
  the name is what both sides are built from, so moving it moves the needle and
  the text together and proves nothing. With `arg in l` inverted to `arg not in
  l`, five assertions in the file went red and the one about `match` stayed
  green - the first of the five being

  > `FAIL: include keeps the matching lines: ['interface Gi0', ' ipv4 address
  > 10.0.0.1/24', 'interface Gi1', 'router bgp 65000', ' neighbor 10.0.0.2
  > remote-as 65001']`

  The check now compares against the lines, the way the `include` check three
  lines above it already does, and the same mutation reddens six:

  > `FAIL: match is the same filter under the name operators type: ['interface
  > Gi0', ' ipv4 address 10.0.0.1/24', 'interface Gi1', 'router bgp 65000', '
  > neighbor 10.0.0.2 remote-as 65001']`

  **What the sweep behind it drove**, because a single finding is worth as much
  as the search that produced it is wide. 34 files, 21,273 lines, 1,744
  assertion sites: 1,620 `check(cond, msg)` and 124 `bad(msg)`, whose condition
  is the `if` above them, and no bare `assert` anywhere. 2,258 `check` calls run
  on a green tree over 1,599 distinct sites; the 21 sites never reached are all
  `check(False, ...)` failure reporters, and of the 124 `bad()` guards every one
  is evaluated except four that sit on the far side of a branch always taken.
  The four shapes named in the hunt were each swept and the last three came back
  empty, which is a result and is written down here so nobody drives them again:
  no Python check condition contains `*`, `/`, `//` or `%` at all, so the
  fixture-arithmetic shape that `24 % 4 == 0` gave the C harnesses has nowhere
  to live; 18 sites whose `in` ran only against an empty container were read and
  every one is empty BECAUSE of the product's answer; and 592 positive needle
  matches were witnessed line by line, with the ones answered by a column header
  rather than a row checked by hand. The shape that did hold is the first:
  eight sites answer both halves of one comparison with the same
  product-backed call. The other seven are each falsifiable and every one was
  shown red on its own anchored mutation, so none of them is being taken on
  trust: `ospf_state` and `isis_findings` when their parsers are made to read
  indentation, `advertisement_fingerprint` when the fingerprint is widened to
  lines that advertise nothing and again when it is emptied, and
  `config.global_key` with the single special case it has taken out.

* **`make test` reached the network, and `TESTING.md` said it did not.** The
  assertion added earlier tonight that the release gate asks
  `tools/roadmap-state.sh` drove the real gate, which runs `gh auth status` and
  then queries the tracker - so every `make test` made two live GitHub calls,
  on a suite whose own documentation says it "requires no root, no VPP and no
  network". Flaky by construction, and false on a machine with no route out.
  Measured with a recording `gh` on the path: **two calls before, none after.**
  The tracker is a stub now, which does not weaken what is being measured: the
  assertion is that the gate **asks** the holder and repeats its answer, and
  both sides read the same stub, so a gate that stopped calling
  `roadmap-state.sh` still diverges from it. The defect and the document
  disagreeing is the shape this repository has spent the night closing;
  whoever wrote that case committed it, and that was me.
  The sibling suite `test/test_release_gate.sh` had it too, for the same
  reason and with the same two calls, and `test/test_run_tests.sh` now refuses
  it for good: a recording `gh` and `curl` on the path, over both suites, so a
  tool that grows a call tomorrow is caught. It is a recording binary and not a
  read of the source, because a suite that shells out through a helper is
  invisible to a grep.

* **The rest of `tools/`, measured refusal by refusal, and the one that could
  never have fired.** `tools/release-gate.sh` was swept yesterday; this is
  every other script in that directory. Read rather than grepped, the thirteen
  that carry refusals hold **72 sites, of which 33 had ever been rendered by
  anything**. **70 are now.** The two that are not are in
  `tools/test-install.sh`, need a booted installation medium, and are named
  below instead.

  | tool | refusals | rendered before | now |
  |---|---|---|---|
  | `wiki-push.sh` | 10 | 3 | 10 |
  | `wiki-pull.sh` | 5 | 2 | 5 |
  | `wiki-status.sh` | 5 | 2 | 5 |
  | `roadmap-state.sh` | 14 | 0 | 14 |
  | `run-all-labs.sh` | 20 | 17 | 20 |
  | `run-tests.sh` | 6 | 6 | 6 |
  | `plugin-version-h.sh` | 3 | 1 | 3 |
  | `plugin-coverage.sh` | 2 | 0 | 2 |
  | `iso-ready.sh` | 2 | 2 | 2 |
  | `vty.sh`, `lab.sh`, `build.sh` | 1 each | 0 | 1 each |
  | `test-install.sh` | 2 | 0 | 0 |

  `version-stamp.sh` carries none: every path it has prints a stamp and exits
  0, which is the point of it. `selftest-verdict.sh` is a sourced list of
  strings.

  **`wiki-pull.sh`'s "nothing came back" was dead code.** The guard was `[ -s
  "$TMP/docs.json" ]`, and the reader above it prints `[]` for an answer
  holding no published documents, which is two bytes and passes a size test.
  Measured against a stub answering `{"data":[],"pagination":{...,"total":0}}`:
  the rewrite ran, **every page in the mirror was deleted**, `wiki/.ids` came
  back empty, `wiki/.pulled` was written EMPTY, and the script exited 0 saying
  `0 pages`. A mirror stamped as freshly pulled and holding nothing, out of
  the script whose entire first half exists to stop `wiki/` being emptied. The
  guard is now the document count, which is the number it was always about,
  and it is driven in both directions.

  **`wiki-push.sh`'s staleness check had never run in either direction.** It
  is the reason that script exists: the wiki is written by people, the push
  goes over what they wrote, so the live document is read first and compared
  against `wiki/.pulled`. Its own header records the near miss - four pages
  updated through the API, a mirror never refreshed, a later edit built from
  the stale copy while a category was being renamed, and nothing lost only
  because the stale text failed to match. Six more of its ten went the same
  way: a mirror that was never pulled, a live document that cannot be read, a
  wiki that takes the request and refuses it, and the three argument checks
  before any of it.

  Every assertion below was shown red on a mutation ANCHORED TO ITS OWN LINE,
  and each one reddened its own case and no other. Two of them are worth
  quoting because the red is the defect in one line:

  ```
  push-stale     if updated > pulled:  ->  if False:
      FAIL: a document edited after the pull was published over anyway
      FAIL: it refused and published the page anyway

  pull-empty     the document count guard  ->  :
      FAIL: an answer with no documents in it rewrote the mirror
      FAIL: the pull refused and emptied wiki/ anyway
  ```

  **No wiki was reached and there is no `WIKI_TOKEN` in this work.** `curl`,
  `gh` and `docker` are stubs on `PATH` answering from fixtures, the way
  `test/test_wiki_roundtrip.sh` already stubbed `curl`. `PATH` is prepended
  and not replaced, except in the one case that needs `gh` to be absent - and
  that case's first run went red with `sh: not found`, over a `PATH` so short
  that nothing started, which is why it asserts the script's own sentence and
  links `sh` and `dirname` in beside the absence.

  **`tools/roadmap-state.sh` had nothing driving it at all.** Only
  `release-gate.sh` runs it, and it runs it where `gh` usually cannot answer,
  so the one branch ever executed under a test was the one saying the
  comparison did not happen. All fourteen are driven now against a stub
  tracker, including the four findings themselves and the check that refuses
  when the `What is blocked on somebody else` section it reads has been
  renamed away. Two of the fourteen - `STATES=$(mktemp) || exit 2` and the
  same for `ERRS` - **print nothing at all**, which is the only pair in that
  file that does not say `NOTHING WAS COMPARED`. They are asserted as they
  stand rather than as they ought to be, and the silence is recorded here.

  **`tools/test-install.sh`'s two refusals cannot be arranged here.** Both
  read the log of `lab/install-iso.sh` after it has booted a medium and been
  refused a login, so driving them means a privileged container, an ISO and a
  QEMU boot - which is a release gate phase and not a `make test` suite. They
  are the control that keeps the install driver honest: `the control PASSED`
  and `the control failed, but NOT at the login`. Named, not closed.

  **`tools/run-all-labs.sh`'s last three.** Seventeen of its twenty were
  already driven against stub labs. The three that were not: the plugin build
  failing, which is the guard that stops the suite before a single lab is run
  against a plugin that was never built; `report_off()`'s third state, an off
  run that printed no verdict either way and so has shown nothing about
  whether its assertions can fail - the defect the off runs exist to catch,
  sitting inside the machinery built to catch it; and the WORKING TREE half of
  the mid-run check, which is the likelier half on this machine, because an
  agent saving a file under `lab/` moves HEAD not at all and the labs either
  side of the save still measure two different products.

  `tools/plugin-version-h.sh`'s two `${N:?usage}` guards needed three calls
  and not two: with no arguments BOTH fire, so a case that only passed none
  would have stayed green with the first deleted. An EMPTY first argument is
  the call only `${1:?}` refuses, and it is the realistic one - a caller
  expanding a variable that is not set. The backslash half of its quote check
  had never run either: a lone backslash does not close the string literal, it
  escapes the quote that ends it, so the compiler swallows the next line and
  reports a token further down a file nobody wrote.

  `tools/lab.sh`'s and `tools/build.sh`'s refusals sit ABOVE their docker
  socket test, so they are driven with a `docker` stub that records every verb
  rather than answering one - and the cases assert that no container was asked
  for. That is not decoration: with `lab.sh`'s check deleted, the mutation run
  went red on `it refused and started a container anyway` with the whole
  `docker run --rm --privileged` line under it.

* **The nine refusals the `appliance/` sweep named as renderable here, and
  left.** That sweep read 113 refusal sites in the scripts that put this
  product on somebody's disk, found 51 had ever been rendered by anything,
  raised it to 77, and listed nine more as reachable without a real machine:
  `write_network_config`'s four write failures, `missing tools:` and `live
  filesystem not found` on a run that means it, the empty disk menu through
  the body rather than at the seam, `this medium has no mdadm`, and
  `appliance/upgrade`'s `There is no dpkg-query here`. None of the nine was
  covered by anything; all nine are now. Every assertion below was shown red
  on a mutation ANCHORED TO ITS OWN LINE, which is not a formality: there are
  two `command -v mdadm` in `hexarouting-install` and a `sed` without a line
  number moves both, so the red would be about a line nobody was claiming.

  **`write_network_config`'s four write failures**
  (`appliance/hexarouting-install:665`, `:700`, `:715`, `:731`), against a
  root that will not take a write. By the time this function runs the disk has
  been partitioned and the filesystem unpacked, so a target mounted read only,
  a filesystem that filled during the unpack, or an `unsquashfs` that exited 0
  having produced no `/etc` all arrive here with the install otherwise
  finished - and a `write_network_config` that reported success would hand
  back a box that boots with its card down and no address, found by whoever
  drove to the site.

  The four are SILENT. Each is a bare `return 1` and the caller turns all four
  into one sentence, so nothing outside the function can say which one fired;
  the only thing that can is how far the write got, and that is what each case
  asserts. Shown red one line at a time, with `return 1` replaced by `return
  0` at that line and nowhere else - and each mutation reddens its own case
  and no other, which is the evidence that the four fixtures land where they
  claim to:

  * `:665` - *"FAIL: a root that cannot be given /etc/network/interfaces.d was
    reported installed: rc=0, and the tree holds []"*
  * `:700` - *"FAIL: a root whose interfaces.d cannot be written was reported
    installed: rc=0, and the stanza file is [ls: cannot access
    '.../etc/network/interfaces.d/hexarouting': No such file or directory]"*
  * `:715` - *"FAIL: a stanza was written into a directory nothing sources and
    the install was reported done: rc=0, and interfaces holds [# nothing here
    reads interfaces.d auto lo iface lo inet loopback ]"*
  * `:731` - *"FAIL: the DNS answer did not reach the box and the install was
    reported done: rc=0, and resolv.conf holds []"*

  They are arranged with mode bits, and mode bits do not stop root, so the
  four say so and skip when the suite is run as root.

  **`missing tools:` and `live filesystem not found` on the real run**
  (`:990`, `:992`). Both are `fatal`, which is a note in check mode and a
  refusal in a run that means it, and only the note had ever happened. They
  are driven through the same arrangement as the confirmation prompt -
  `unshare -r`, an unbound `/dev/loop0`, every tool stubbed on PATH ahead of
  the real one - so a run that got past either would reach a partitioner that
  appends to a file rather than a disk.

  **The obvious arrangement for the tool list is the wrong one, and it cost
  two false greens before it was.** `command -v` searches PATH, so a tool
  cannot be hidden by putting something in front of it: the run has to be
  handed a PATH the tool is not on. Cutting PATH down to the stub directory
  does that, and the first attempt at it left `sh` off - `env` could not start
  the installer at all, so nothing ran, nothing was partitioned and the exit
  status was not zero, and the case's first assertion read as a pass over a
  product that had not been executed. Putting `sh` back fixed that and left
  the second: with the tool check neutralised, a cut-down PATH means the run
  dies on the next command it cannot find, so *"it refused and nothing was
  partitioned"* stays green over a product that no longer refuses. What works
  is the opposite - leave the PATH alone and take away the stub for a tool
  THIS MACHINE does not have either, chosen at runtime from the installer's
  own list and ordered latest-used first, so that a run getting past the check
  reaches the partitioner. The message is composed from whatever is missing,
  so *"missing tools: update-grub"* with no second name in it is also the
  proof that the other nine were on the PATH the case ran with. With `:990`
  neutralised: *"FAIL: the install ran on a medium with no update-grub on it:
  rc=1, the partitioner ran 1 time(s)"*. With `:992` neutralised: *"FAIL: the
  install carried on with nothing to unpack: rc=1, the partitioner ran 1
  time(s)"*.

  **The empty disk menu, through the body** (`:1001`). That `choose_disk`
  refuses an empty machine and says what to do about it is asserted at the
  library seam and was already; what had never run is `DISK=$(choose_disk) ||
  exit 1`, the line that READS it. So the new case asserts only what the seam
  cannot reach, which is WHICH sentence the operator gets: with the `|| exit
  1` dropped the run carries on to the check two lines below and prints *"no
  disk given"*, a sentence about an answer they did not give rather than about
  a machine with nothing in it to install onto. *"FAIL: the menu refused and
  the run carried on to the check below it, so the operator is told they gave
  no answer rather than that this machine has nothing to install onto"*.

  **`this medium has no mdadm`** (`:1159`), which is the second of the two
  `command -v mdadm` and is asked down where a mirror was actually answered,
  because a medium without `mdadm` must still install a one-disk box. It needs
  a second disk that passes every check above it, and `/dev/loop1` unbound is
  zero bytes and would be refused for being too short - so the mount namespace
  the real run already has is given a 2 GB answer bound over
  `/sys/block/loop1/size`, which is the only place the installer reads a
  mirror's size from. Nothing is bound to the device and nothing is written to
  it. THE CONTROL IS THE ARRANGEMENT PROOF: with the `mdadm` stub present the
  same run has to get past the mirror question, which it can only do if
  `/dev/loop1` was taken as a whole second disk wide enough for the records,
  and the summary says *"records mirror /dev/loop1"*. With `:1159`
  neutralised: *"FAIL: a mirror was accepted on a medium that cannot build
  one: rc=1, the partitioner ran 1 time(s)"*.

  **`There is no dpkg-query here`** (`appliance/upgrade:153`). The platform
  check is asked of the box, and `dpkg-query` is the half of "ask the box"
  that is not a file the script can look for. Driven with a PATH holding the
  four commands `appliance/upgrade` uses before that line and nothing else,
  against the suite's ordinary fixture box; the control adds a `dpkg-query`
  to the same PATH that answers a version no release was built against, so the
  run is stopped by the platform check instead and the PATH is proved not to
  be what refused. What the refusal is worth is the sentence, not the refusal:
  without it `have()` comes back empty for both packages and a box carrying a
  perfectly good database is told it has no VPP installed at all, which sends
  its owner looking for a package that is on the disk. *"FAIL: a box nothing
  could ask was told it is not an appliance at all, about a package database
  that has vpp 26.06-release in it"*.

  **Found and not fixed, because it is a product change and not a test.** The
  four `return 1` in `write_network_config` carry no diagnosis of their own
  and the caller cannot invent one: an operator reading *"could not write the
  management network onto the target"* is not told whether the target had no
  `/etc/network` at all, whether it would not take the stanza, or whether the
  stanza went down and `/etc/network/interfaces` could not be made to read it.
  The last of those is the one that leaves the most behind - a perfect file in
  a directory nothing sources - and it is the only one where the box is
  otherwise complete. The four states are now written down in
  `test/test_installer.sh`, which is where somebody adding the messages would
  look to see what there is to say.

* **A failing installer case reported the previous case's output.**
  `test/test_installer.sh`'s `run()` set `LAST_OUT` only where the case passed,
  and both failure paths returned before it - so the 63 `case "$LAST_OUT" in`
  assertions that follow every run in that file described the box before the
  one that had just failed. The first failure in a run made every message after
  it about the wrong machine, at the one moment somebody is reading them.
  Driven side by side over the same three cases, the third failing: the old
  helper leaves the follow-up reading `output of case B`, the new one `output
  of case C`. The refusal path also prints what the installer said, which it
  did not - `expected refusal, it was accepted` on its own sends a reader to
  re-run it by hand to find out what happened instead.

* **The installer said a disk was held by an array on a box that has no
  arrays, and then partitioned it**
  ([#339](https://github.com/andrediashexa/hexarouting/issues/339)).
  `release_disk()`'s two early returns - no `mdadm`, no readable
  `/proc/mdstat` - returned 0, and the caller reads non-zero as "the disk is
  free". So "I could not look" arrived as "still held, try again": four rounds
  later the installer printed `<disk> is still held by an array after 4
  attempts`, dumped `/proc/mdstat` to the console, and partitioned a disk
  nothing was holding. And the refusal under it was a message only -
  `release_disk_until_free()` ends in `return 1` and **both** call sites
  discarded it, so a disk an array genuinely will not release was partitioned
  too, which is the one outcome that function's own comment says it exists to
  prevent. Three answers now - released, free, nobody here can be asked - and
  the refusal is read. Found by the sweep of `appliance/`'s 113 refusal sites,
  of which 51 had ever been rendered by anything.

* **A whole arm of the dynamic allocator had never been executed by any C
  harness, and nothing could have said so.** The ten harnesses under `test/`
  ran 65,376 checks when this was written, and **65,767** on this tree; their
  verdict is a count of checks, not of product reached - so a guard nobody
  enters looks exactly like one everybody enters.
  Measured with `gcov` for the first time: 108 of the plugin's 4,807
  executable lines are never touched by any of them, and twelve of those were
  the block-stride placement in `bpa_dyn_acquire_extra()`, with both of its
  counters, behind `p->block_stride != 0`. Every pool those harnesses build has
  stride 0, because the one function that makes them memsets and never sets
  one. `lab/extblocks.sh` and `lab/dynamic-workers.sh` do reach it, so the path
  was not unmeasured - it was measured only where measuring costs a container
  and a release run, and a regression in it went green through every
  docker-free suite. `tools/plugin-coverage.sh` makes the measurement
  repeatable, prints a count and no verdict - a line nobody executes is a
  question, not a sentence - and refuses when it read no counters at all.

* **The refusals in `appliance/`, swept, and 26 of them had never run.** The
  scripts that put this product on somebody's disk and bring it up refused in
  113 places across eight files the day of this sweep, counted by reading each
  one rather than by
  grepping for `exit 1`: `hexarouting-install` 79, `build-iso.sh` 8, `upgrade`
  7, `bind-dataplane-nic` 6, `build-upgrade.sh` 6, `apply-tuning` 5,
  `install.sh` 2, and `ssh-host-key-origin` 0 - that last one never refuses
  anything, it classifies, and saying so is part of the measurement. 51 of the
  113 were rendered by something. 26 more are now, and the rest are named
  below with what each would take.

  It is 115 and `hexarouting-install` 81 now. The #339 entry above this one is
  what added the two: `git diff` over those eight files since this sweep shows
  no other refusal added or removed, and what it added are the two `die`s at
  the call sites of `release_disk_until_free`, one for the disk and one for
  the mirror. They are named by their function and not by a line, because
  `appliance/hexarouting-install` has moved every line past 1081 twice since
  this entry was written and a number here goes stale faster than the sentence
  around it.

  **The one at the top of the list had no test on either arm.** `Type 'yes' to
  install:` is the last thing between an operator and a destroyed disk, and
  every case in `test/test_installer.sh` ran under `HEXA_INSTALL_CHECK=1`,
  which exits at the summary forty lines above that prompt. `lab/install-iso.sh`
  cannot reach it either: it installs unattended with `HEXA_INSTALL_YES=1`,
  which skips the question by design, and a passing install cannot demonstrate
  a refusal. So `no`, a bare Enter and `y` were all unmeasured, and so was
  everything after the prompt - `sfdisk failed`, `sfdisk timed out`,
  `partitioning did not produce`, the messages an operator reads with the disk
  already open for writing.

  They are driven now, by a real run arranged so that three separate things
  have to fail before it can reach a disk: `unshare -r` for the uid, because
  `id -u` is the installer's own check and a seam past it would be the check
  removed; an unbound `/dev/loop0`, which is a real block device backed by
  nothing and reporting zero bytes, skipped unless `/sys/block/loop0/size` is
  0; and every tool the installer names stubbed on PATH ahead of the real one,
  `sfdisk` first, so that a run which got past a refusal reaches a shell script
  that appends to a file. The assertion is that file. Measured with the
  confirmation replaced by `|| true`: *"FAIL: the disk was partitioned after
  the operator said no"*, and `y` and the return key erased a disk in the same
  run. `unshare -m` gives it a `/tmp` of its own, so the `/tmp/sfdisk.log` the
  installer quotes to the console is this suite's file and not the machine's.

  **Thirteen more in `hexarouting-install`.** The mirror named as the boot
  medium (the target has been refused for it since it was written and the
  second disk never was - it is destroyed by the same `yes`, and refusing it
  late leaves an operator with neither the old box nor the new one and no
  medium to install from); a gateway that is not an address at all, where what
  is asserted is the DIAGNOSIS and not the refusal, because measured with the
  shape check bypassed the subnet arithmetic still says no and says *"is not on
  192.0.2.10/24's subnet"* about a value that is not a subnet; an unreadable
  answer file, a line with no `=` in it, and `yes = maybe`; the library seam
  RETURNING rather than exiting, which is what `install-from-cmdline` depends
  on to say what happened instead of leaving a machine at a prompt; a machine
  with nothing in it but the medium; an `/etc/fstab` that was never written, as
  distinct from the empty one already covered; the password prompt that runs
  out of input at the repeat; and a real non-root run, which refuses before the
  banner.

  **And in the rest of `appliance/`.** `build-iso.sh` had been executed by
  nothing at all - every reader of it in this repository reads it as text - so
  its four preflight refusals are now driven by `test/test_build_iso_preflight.sh`
  against a stand-in tree, with `$WORK` handed a directory carrying a file: `rm
  -rf "$WORK"` is the first destructive line and it is below all four, so the
  file surviving is the evidence the refusals came first. `install.sh` refused
  a tree with no `bpa_plugin.so` at all and nothing had ever run it; with that
  refusal removed the run *"moved the version stamp from 0.72.0 (beefcaf) to
  0.73.0 (dddddd)"* over a plugin it did not have. `bind-dataplane-nic` gets
  its two sysfs writes, and the second one matters more than it looks: a
  refused unbind leaves the device on its old driver either way, so the exit
  status cannot tell that case from a driver that declined the device and the
  message is the only thing that can. `upgrade` gets a damaged `RELEASE` (all
  three lines, and without the check a missing `stamp=` stamps the box
  *"unknown (not a git tree)"*, which is #302 arriving through the artifact),
  a root with no package database, and its own non-root refusal.
  `build-upgrade.sh` gets the other half of the plugin pair and the install
  proof it calls its own. `apply-tuning` gets the last one it had: a box with
  no `update-grub`, where the kernel command line has already been written and
  nothing can apply it.

  **What still needs a real machine, named.** 16 of `hexarouting-install`'s
  refusals sit past line 1485, where the install has stopped being about
  answers and started being about a partitioned device: `mount`, `unsquashfs`,
  `grub-install`, `update-grub`, `chpasswd`, the ssh host keys on the target,
  the records array unit, and the final `did not verify`. Reaching them needs
  partitions that really appear, which means a losetup-backed image and real
  root. The four refusals in `build-iso.sh` below its preflight want a
  debootstrapped chroot for the same reason. Eight more are renderable here and
  are not done yet - `write_network_config`'s four write failures against a
  read-only root, `missing tools:` and `live filesystem not found` on the real
  run, the empty disk menu through `main` rather than at the seam, and `this
  medium has no mdadm`; `upgrade`'s `There is no dpkg-query here` is a ninth.

  **Four things found and not fixed, because none of them is a test.** Two of
  the four were fixed later the same day, by the
  [#339](https://github.com/andrediashexa/hexarouting/issues/339) entry above:
  `release_disk()`'s two early exits return **2** rather than 0, and
  `release_disk_until_free`'s `return 1` is read by both of its call sites,
  which `die`. The paragraph below is what was true when it was written, and
  the line numbers in it no longer point at what they name - every line past
  1081 of `appliance/hexarouting-install` has moved twice since. The other two
  of the four stand.

  `release_disk()` returns 0 from both of its early exits
  (`appliance/hexarouting-install:1379` and `:1380`) and its caller reads
  non-zero as *"the disk is free"*, so on a box with no `mdadm` or no readable
  `/proc/mdstat` the installer prints *"is still held by an array after 4
  attempts"* and a copy of `/proc/mdstat` before partitioning a disk nothing
  holds. It does not bite a real medium, because `build-iso.sh` asserts `mdadm`
  reached the image. `release_disk_until_free`'s `return 1` (`:1435`) is
  discarded by both of its call sites (`:1438`, `:1498`), so the refusal its
  comment describes is a message. `:1264`'s `die` cannot be told apart from
  `:1257`'s from outside the program - with it replaced by `|| true` the retry
  loop refuses the same input with the same words, so what it is worth is the
  diagnosis, and that is what is asserted. And `build-upgrade.sh`'s comment
  offers `src/hexash/config.py` as the file its install proof catches:
  `install.sh:251` installs `src/hexash/*.py` through a glob, deliberately, so
  a missing `config.py` installs one file fewer and exits 0 - measured, *"31
  file(s) installed"*, and the artifact was packed. The case uses
  `appliance/wait-for-vpp`, which is installed by name, and says why.

  Every assertion above was shown red on its own mutation of the product and
  the product was restored each time; `make test` is 63 suites green.

* **Six C assertions that could not fail, and the suite that refuses the next
  one.** `test/` holds 10 C harnesses that compile against the BPA plugin's
  headers with `-DBPA_STANDALONE`, 382 `CHECK` sites between them, 65,376
  checks on a run - the figures the sweep was over, and both have grown
  since: `make test` reports **65,767** checks on this tree, over the sites
  the coverage work two entries above added. They are the only thing in this
  repository that measures the dataplane's arithmetic without a container, and
  nobody had ever swept them. This is that sweep, and it is the same sweep as
  the lab one above, one layer down.

  Sixteen product mutations were driven to settle it, each one an edit to
  `src/plugins/bpa/*` that still compiles under `-Wall -Wextra -Werror`, runs,
  and gets a real answer wrong. A mutation the compiler rejects proves a branch
  exists and nothing else, so none is counted here. Every repair below is
  recorded with the mutation that turns it red.

  Two of the findings are whole behaviours no assertion could see.
  `bpa_portmap_alloc_pair()` is what lands an RTP port on an even number, and
  all 4905 checks of `test/test_bpa_ports.c` stayed green with its scan stepping
  by one instead of two, and green again with the test that the PARTNER port is
  free deleted. The first hands out an odd RTP port, which fails as "no video";
  the second hands a subscriber a port another flow already holds and drops
  `n_free` by two for one port taken. Both survived because every fixture fed
  the pair scan an empty map, where the offsets come back even by the loop
  header rather than by any decision, and because the one case written for a
  refusal was answered by the `n_free < 2` guard before the scan ever ran. Two
  fixtures now leave exactly one non-conforming pair free.

  `test/test_bpa_icmp.c` reuses one `bpa_icmp_err_t` for every parse, and every
  fixture quotes the same two addresses, so the ESP block's four expected values
  were already in the struct before the call. All 40 checks passed with the ESP
  branch cut down to two fields, and again with the GRE branch's two address
  writes deleted - both of them the #203 failure, a lookup built from whatever
  the bytes happen to be. Each parse now starts from a poisoned verdict.

  The other four are assertions whose subject was elsewhere.
  `test/test_bpa_rtsp.c` asserted `cp.hi == cp.lo + 1`, which is the parser's own
  `if (hi != lo + 1) return BPA_RTSP_UNPARSED` said a second time: shifting both
  parsed ports by 100 left all seven matching cases green, and only the single
  round-trip case at the foot of the file noticed. It now asserts the ports
  against the digits in the message. In `test/test_bpa_dyn.c`, six of the eight
  worker counts in "the step holds the address at every worker count" were
  answered by the fixture rather than by the allocator: 24 arrivals over 4
  public addresses made `24 % 4 == 0` realign every extension by accident, and
  at 4 and at 8 workers a slice spans one address, where "it landed on its own"
  is arithmetic and not a choice. It now arrives 26, and checks per worker count
  that the free stack really held a block elsewhere. `test/test_bpa_auto_mss.c`
  gave its second interface the same MTU as the first, so a repeated attach
  counted twice reached 1460 whatever `n_at_min` held; the second interface is
  now wider. `test/test_bpa_ftp.c` never reached the second term of
  `if (rev->off_after || rev->off_before)`, so an acknowledgement before the
  correction point of a session whose drift returned to zero went back
  uncorrected with all 58 checks green.

  One coverage hole found on the way, in the same shape: `bpa_sip_edit_add()`'s
  width bound was probed only from above, so `tlen > BPA_SIP_TXT_MAX` written as
  `>=` refuses the widest legal replacement - the 21 bytes of
  `255.255.255.255:65535` that the constant is sized for - and leaves those
  messages untouched, with all 107 checks of `test/test_bpa_sip.c` green. A
  bound probed from one side is half a bound.

  Refuted, and recorded so nobody redoes it: the pair-step congruence at
  `test/test_bpa_pool.c:620` reads as a tautology and is not one. It is blind to
  a wrong `bpa_stride_unit()`, because both of its operands move together, and
  it does catch a wrong `bpa_pair_step()` - 0 reds against the first mutation, 5
  against the second. It is one-sided rather than vacuous and is left alone. So
  is the one-address control at the foot of `test/test_bpa_dyn.c`, which cannot
  fail by construction, says so in its own comment, and exists to show what the
  check above it needs four addresses to avoid.

  `test/test_c_restates_product.py` refuses the next assertion that quotes the
  product's own refusal back at it. It pairs each harness with the files it
  includes, reduces every `CHECK` and every `if (...) return/break/continue` to
  one comparison, and reports a `CHECK` that is a guard's negation. It read 294
  of the 382 sites against 74 guards and flagged none the day it was written;
  it reads **429** against 74 on this tree and still flags none, which is the
  number `test/test_c_restates_product.py` prints on every run; against the
  RTSP harness as it stood this morning it names line 60 and the guard. It is
  the sister of `test/test_lab_vacuous.py`, which asks the same question of the
  shell labs, and between them both of this repository's assertion idioms are
  swept. What neither can see is stated in its header rather than left to be
  discovered: a guard reached through a helper or expressed as a loop bound is
  out of reach of a source check, and the answer for those is still to delete
  the product behaviour and watch the assertion stay green.

* **The `## Unreleased` section said `### Fixed` twelve times, and only the
  release would have found out.** Every branch adds its entry under `### Fixed`
  or `### Added`, git resolves none of it because they are different lines, and
  the section accumulates one heading per branch - measured here at 12 `### Fixed`
  and 4 `### Added` over 37 entries. That section becomes `## <version>` the
  moment a release is cut, and `tools/release-gate.sh` refuses a version section
  that repeats a heading. So it would have been found with a tag in hand and an
  image built, over something every merge could have caught for free.
  `test/test_changelog_landing.py` catches it at the merge now, and the section
  is consolidated with every one of the 37 entries kept in the order it landed.

* **The negative half of the lab assertions, swept, and one of them could not
  fail.** The sweep below this one read every `expect*` call site in `lab/` for
  a red branch nothing renders; this one reads the 141 that say a thing must be
  ABSENT, which is a different question with a different way of going wrong. `expect_not_in` and `expect_not_said` say *"the box must not print
  X"*, and that is the direction nobody notices: a needle that can never match
  makes a positive check red on a correct box, which somebody chases, and a
  negative check green on a broken one, which nobody does. The sweep counted
  them itself rather than trusting a figure. 141 in `lab/*.sh`, 128
  `expect_not_in` and 13 `expect_not_said`, counted by joining shell
  continuations and quoted strings that span source lines and matching the
  helper at the start of a statement; one `expect_not_in` in `lab/vty.sh:785`
  is inside a comment and is not one. Beside them the labs hold twelve more
  assertions that are negative without being those helpers, and they were read
  too: four `expect ... sh -c "! vppctl ..."` in `lab/igp.sh`, six
  `expect ... test -z` in `lab/esp.sh` and `lab/pptp.sh`, and the two
  bad-answer needles `expect_one_of` takes in `lab/install-iso.sh`. 153 in all.
  No lab could be run: a soak still holds the container.

  **Can the product print the needle at all.** 134 of the 141 needles are
  literal. Screened against `src/plugins/bpa/*.c`, `src/hexash/hexash.py`,
  `appliance/*` and `tools/*`, with adjacent string literals joined and
  whitespace collapsed on both sides, 42 came back absent. That is the same 42
  `test/test_doc_copies.py` records from the last time this was tried, which is
  what says the screen works and not that 42 needles are wrong. Every one was
  checked by hand and every one is a false positive. Most are composed at run
  time from a fixture the lab built: `pool z `, `pool client-bad`, `pool
  doomed`, `del pool fwd`, `PL-BAD`, `PL-MINE`, `hexro`, `not:an:address`,
  `HEXA_DATAPLANE_IF=box0`, `pg0.700`, `pg1`, `vppmgmt0`, `10.7.0.0/24` and a
  dozen other addresses. Six more are FRR's words rather than this product's:
  `gracefulShutdown`, `pseudo interface`, `router isis HEXA`, `description
  uplink`, `Waiting for children`, `frr: '`. `ospf6d is not running` is built
  by `hexash.py:6218` out of `ROUTER_DAEMON`. The two sentences in
  `lab/upgrade.sh` are printed by `appliance/install.sh` across two `echo`
  lines each, at 648 and 677, which is why a literal search cannot see them and
  why both use `expect_not_said`. `carries OSPF and its MTU is` reaches `show
  ospf interface` through `mtu_stall_lines()`, called at `hexash.py:11235` as
  well as at commit check. `carries no per-interface description` is a sentence
  the product deliberately no longer prints, and its wording is pinned by
  `docs/routing.md:240` and by `test/test_hexash_complete.py` twice. So there
  is no finding in this half, and saying so with the numbers is the point:
  nobody should spend another afternoon on it.

  **Is the haystack there.** The other half of `lab/routing.sh`'s
  `host-veth2-vpp`: a negative assertion over output that is empty for a reason
  unrelated to its subject is green for the wrong reason. Measured: 29 of the
  141 read a variable no other assertion in that lab reads. A rule demanding
  every negative haystack be pinned non-empty would flag all 29, and most are
  correct - `$FLOOR_OK`, `$QUIET`, `$STRIDE_OK`, `$BOOT_GONE`, `$LOAD` are all
  *"the box said nothing"*, where an empty answer IS the pass. That rule needs
  an allowlist, so it was not written. Two narrower sweeps were run instead and
  found nothing: no negative assertion names an interface, pool or card that
  appears nowhere else in its own lab except `lab/ipfix-tcp.sh:162`, which was
  already recorded as unsettled and whose `vppmgmt0` is created by the lab at
  line 41; and exactly one negative needle carries column alignment,
  `lab/bgp-policy.sh:680`, which is pinned by its own positive twin one line
  above it.

  **`lab/vty-ssh.sh` asked a question its own failure case cannot answer.**
  *"and the address it refused is not in effect"* searched
  `EFFECTIVE=$(sshd -T 2>/dev/null | grep -i "^listenaddress" | head -2 ...)`
  for `not:an:address`. `sshd -T` refuses a file it cannot parse instead of
  printing a partial one, so on the box this check is about - the one that left
  `ListenAddress not:an:address` in `sshd_config` - it exits 255, writes
  *"bad addr or host: not:an:address (Name or service not known)"* on STDERR,
  and prints no configuration at all. `2>/dev/null` threw the refusal away, the
  grep had no `listenaddress` line to select, and the needle was searched for
  in an empty string. Driven on OpenSSH 9.2 over two real `sshd_config` files,
  with that capture line and the assertion copied out verbatim: the box that
  rolled the address back reads `[ ok ]`, and the box that did not reads
  `[ ok ]` as well. It could not fail in either direction, and the section's
  whole subject is that second box.

  The capture now keeps stderr, so the refusal itself is in the haystack, which
  is where the address the box must not be using appears; and a positive beside
  it says sshd answered at all, so a future sshd that stops answering or
  rewords its refusal makes the pair red rather than vacuous again. That is the
  same repair `lab/routing.sh`'s `block_of` pair took. Driven over the same two
  files: the good box reads `[ ok ]` twice and the bad box reads
  `[FAIL] sshd could be asked what it would run with at all (missing:
  listenaddress)` and `[FAIL] and the address it refused is not in effect
  (unexpected: not:an:address)`. `expect_in "so sshd would still start"` two
  lines below already went red on that box, which is why this is a repair and
  not a new subject.

* **`test/test_lab_negative.py`** refuses the two shapes that have actually
  been found here, and refuses nothing else. A check on the SOURCE, like the
  two suites beside it, for the reason they both give: an assertion that cannot
  fail is invisible on a green box.

  The first rule is that `expect_in` and `expect_not_in` may not be given a
  needle with a NEWLINE in it. They search the text as it came, so such a
  needle matches only where the output happens to break, and every one written
  in this repository has been a prose sentence typed across two source lines
  with the closing quote in column 1 - which is `lab/routing.sh:950` above.
  `expect_said` and `expect_not_said` collapse whitespace on both sides and
  exist for this.

  The second is that a negative assertion may not read a haystack built by
  DISCARDING STDERR and then SELECTING LINES unless some other assertion in the
  same lab reads it too. That pair is the one combination where the haystack
  goes empty on precisely the box the check is about: the command refused, said
  why on stderr, the refusal was thrown away, and the selection had nothing to
  pick. `head`, `tail` and `cut` are not in it, because they cannot turn a
  non-empty answer into an empty one. The escape is structural rather than a
  list of excused lines. Measured today: two negative assertions read such a
  haystack, `lab/handoff.sh:140` and `lab/vty.sh:634`, and both are pinned, so
  the rule costs nobody an exception.

  141 negative assertions are in scope and the floor refuses a run that reads
  fewer than 100, because a suite that reads nothing passes, which is the
  defect it is about. That floor earned itself twice while the file was being
  written. The reader has to join quoted strings across source lines or it
  cannot see the first rule's defect at all; counting double quotes alone
  swallowed 14 assertions into the logical line above them, because inside
  single quotes a double quote is an ordinary character and `lab/routing.sh`'s
  `block_of` is a multi-line awk program full of them; and tracking both quotes
  without stopping at `#` swallowed 87, because the labs explain themselves in
  English and *"the operator's own"* leaves a single quote open. Only the
  version that reads 141 is the one that reads the labs.

  Shown failing three ways: on `lab/routing.sh:950` restored to what it was
  before it was fixed, on `lab/vty-ssh.sh:114` as it stands in `master`, and on
  a line joiner deliberately broken, which reports *"the checker does not see a
  needle written across two source lines"*.

  The second of those is a line nobody can follow now: this entry's own repair
  went into `lab/vty-ssh.sh` in the same merge, so `lab/vty-ssh.sh:114` on
  `master` is an unrelated `echo` and the lab reads stderr. The text the
  checker was shown red on is kept verbatim in `test/test_lab_negative.py`
  under the name `VTY_SSH_114`, which is where to read it.

  `make test`: 61 suites, 61 passed.

* **The lab suite could lose a lab and still pass.** `tools/run-all-labs.sh`
  ends with four counters - `pass`, `fail`, `notmeasured`, `novpp`, plus `ran`
  - and exits green when three of them are zero. Those counters are internally
  consistent with each other **whatever happens**: a lab that never reaches
  `judge_batch`, because a batch lost its file or a name produced none, simply
  makes the totals smaller, and the suite reports that everything it ran
  passed. That is a verdict about fewer labs than the repository has, and
  nothing anywhere said so - which is the same defect as a release cut on a
  suite that never measured the thing it cleared. It now counts what it set out
  to run, before anything runs, and refuses when the accounting is short. The
  rule is the one `test/test_doc_copies.py` already applies to documents: a
  counted list says how many it has.


* **Six lab assertions that could not fail, and the suite that refuses the
  next one.** `lab/` holds 62 shell files, 60 of them labs and two of them the
  helpers the rest source, and between them 1381 `expect*` call sites. They are
  the only thing that measures this product against a real dataplane. Nobody had
  ever swept them for checks whose red branch nothing renders. This is that
  sweep. It could run no lab - an eight hour soak held the container, and a lab
  run beside it returns a false verdict - so every finding below was established
  by reading and then DRIVEN, either as arithmetic or by feeding the assertion
  the text a box would have given it. Nothing here rests on a suspicion; where
  one could not be settled it is listed at the end as unsettled.

  What the sweep found first was that most of this ground is already held.
  `tools/run-all-labs.sh` runs 20 of the 60 labs a second time with the feature
  under test switched off and requires each to go red, against a floor written
  into the lab itself; 20 labs declare an `OFF-RED-AT-LEAST` and 20 are given an
  off run, with `lab/upgrade.sh` explaining in a comment why it has neither. So
  425 of the 1381 assertions have had their red branch rendered on every release
  run. The six below are among the 956 that have not - this said "the five
  below" when it was written, over six findings, and six is what is under it.

  1381, 425 and 956 are the count on the tree this sweep read. Three more
  `expect*` call sites have landed under `lab/` since, counted with the line
  joiner in `test/test_lab_negative.py`, which is the only reader here that
  sees an assertion written across two source lines. 60 labs, 62 files, 20 off
  runs and 20 floors are unchanged.

  **`lab/extblocks.sh` asserted that its own arithmetic held.**
  `expect "and one this worker count can honour" test "$((STRIDE % NW))" -eq 0`
  sat twenty lines under `STRIDE=$((2 * NW))`. Driven under dash over worker
  counts 1 to 64, it is `[ ok ]` on every one. It was added as the guard on an
  anti-vacuity fixture, and when the fixture was made to derive its numbers from
  the worker count the guard became a statement about the derivation rather than
  about the box. Deleted, with what it was for recorded where it stood: the
  version with content reads the box's own `cannot be honoured`, which
  `lab/dynamic-workers.sh` already asserts, and writing it needs a container to
  show it red.

  **`lab/ipfix-outbuf.sh` asserted that a byte count was not negative.**
  `test "${DROPPED_STOP:-0}" -ge 0`, described as *"the stop had bytes it could
  not write"*. Driven under dash over `''`, `0`, `1` and `999999`: `[ ok ]` on
  all four. Deleted rather than repaired, because both halves of its subject are
  already held - that bytes were still buffered is the precondition above it,
  which is the one the `no-stall` off run takes red, and `bpa_stop_drain()`
  prints *"could NOT be written"* only under `if (lost)`, so the phrase the line
  below asserts IS the count being non-zero. The `OFF-RED-AT-LEAST: 8` floor is
  unchanged and was not re-measured: an assertion true for every value was green
  in the off run too, so it was never one of the eight. **That floor is 9 now**,
  and the entry above about a floor below what the off run measures is the one
  that raised it: `grep OFF-RED-AT-LEAST lab/ipfix-outbuf.sh` reads
  `# OFF-RED-AT-LEAST: 9`, and the lab's own note at `lab/ipfix-outbuf.sh:776`
  says the floor WAS changed by it.

  **`lab/boot-iso.sh` read the login banner instead of the answer.** The check
  for [#305](https://github.com/andrediashexa/hexarouting/issues/305) greps the
  whole console capture for `installation medium|'install'` after typing `show
  interface`. The medium's own login banner says both of those things, several
  lines before the command is typed, and the writer waits for the prompt the
  banner precedes - so the needle is in the log by construction on every run.
  Driven over two transcripts: a medium answering with the advice, and the same
  medium with `why_no_dataplane_interface()` returning nothing at all, so the
  screen carries an empty table and no advice. Both read `[ ok ]`, and on the
  second it was the banner that said it. The needle now reads only what the
  command answered. The negative half is unaffected and was already sound:
  *"Reboot once the tuning"* has exactly one printer in the tree.

  **`lab/routing.sh` asked about an interface it does not have.**
  `expect_not_in "and not on the other one" "shutdown" "$(block_of
  host-veth2-vpp)"`. `host-veth2-vpp` belongs to `lab/bgp-policy.sh`; it appears
  nowhere else in `routing.sh`, so `block_of` found no such stanza, printed
  nothing, and *"shutdown is not in this empty string"* was green on every box
  the lab has run on. Driven with `block_of` lifted out verbatim and `$HX`
  replaced by a file: with the shutdown on both interfaces, which is the failure
  the line exists to catch, the old name reads `[ ok ]` and the right one reads
  `[FAIL] and not on the other one (unexpected: shutdown)`. The block is now
  pinned non-empty first, so a rename makes this red rather than vacuous.

  **`lab/routing.sh` carried a needle with a newline in it.** *"does not claim
  the whole configuration checks out"* searched for `configuration check
  succeeds\n`, the newline arriving because the needle was written across two
  source lines. `CHECKOUT=$(...)` strips trailing newlines, so the bare
  sentence, which is the last thing that branch prints, loses the byte the
  needle needed; and the other branch continues the line with *"for CGNAT and
  the box's own configuration"*, so no newline follows there either. Neither
  sentence could ever match. Driven over three commit-check outputs - a refusal
  alone, a refusal plus the bare sentence, a refusal plus the narrowed one -
  the old needle is `[ ok ]` on all three and the new one is red on the last
  two. It cannot go red on a correct box: both prints sit inside `if
  self.apply(self.candidate, check_only=True)` and a candidate with a bad prefix
  range never reaches them.

  **`lab/ftp-alg.sh` matched a row label, not a state.** *"and show bpa pool
  reports the switch either way"* looked for `alg          ftp`, which is the
  row label and the first ALG's name. `format_bpa_alg()` emits every ALG's name
  with its state and the row is printed unconditionally, under a comment saying
  so, so that needle is in `show bpa pool` for every pool on every box.
  `lab/pptp.sh` and `lab/esp.sh` each record having fixed this in their own
  file; this was the copy left. The repair had to be scoped as well as
  corrected, and that was measured rather than assumed: driven over two
  renderings of `show bpa pool`, a bare `ftp off` over the whole output is
  `[ ok ]` on a box where the switch never reached the pool, because the second
  pool a few lines down supplies it. Only the needle scoped to the pool that was
  switched goes red.

* **`test/test_lab_vacuous.py`** refuses the shape the first two of those are.
  Every numeric `expect` in `lab/` is expanded through the lab's own arithmetic
  definitions and driven over the values a box can supply; one true for all of
  them cannot fail, and one false for all of them cannot pass. It is a check on
  the SOURCE, like `test/test_lab_crlf.py`, and for the same reason: an
  assertion that cannot fail is invisible on a green box by construction. 216
  assertions were in scope the day it was written and **217** are on this tree,
  which is the number the suite prints; the floor refuses a run that reads
  fewer than 150, because a suite that reads nothing passes, which is the
  defect it is about.

  It folds only definitions that are real arithmetic, never a bare copy, and
  that distinction was measured rather than chosen. `lab/pool-del.sh` writes
  `SMALL=$BEST` after timing the small table and `BIG=$BEST` after timing the
  large one; folding both into `$BEST` reports that lab's central measurement as
  comparing a value with itself. With bare copies folded the suite flags
  `pool-del.sh:263` and nothing else, and that one hit is the false positive;
  with them left alone it flags none. Writing it also cost one bug of its own:
  the two sides of a comparison were joined without a separator, so
  `V_DROPPED_STOP` against `0` read as the single name `V_DROPPED_STOP0`, eval
  raised, and twenty-odd assertions were skipped instead of judged. That is what
  had been hiding `lab/ipfix-outbuf.sh`.

  Shown failing four ways: on each of the two real findings before they were
  fixed, on a tautology and an impossibility planted in `lab/two-pools.sh`, and
  on a matcher deliberately broken, which reports *"only 0 arithmetic assertions
  were read ... this suite is passing by reading nothing"*.

  `make test`: 59 suites, 59 passed.

* **What the sweep could not settle, said rather than left implied.** Two
  candidates were refuted on inspection and are recorded so nobody re-opens
  them: `lab/pool-del.sh:263` is the false positive above, and
  `lab/records-full.sh:208`, which reads only the file's last byte, is right to
  do so - `bpa_audit_verify()`'s own comment states that a fragment IS the end
  of the file, because a write that stopped half way closed no newline. Several
  more need a dataplane and were left: whether `lab/one-nic.sh`'s *"named after
  the card"* can distinguish the Linux name from the VPP one, which it cannot
  today because `box0` is a substring of `host-box0-bound`, but whose repair
  needs the name a real replay chooses; whether `lab/ipfix-tcp.sh:162`'s
  negative over `show lcp` is pinned by anything; and whether
  `lab/install-iso.sh` turns a redrawn `[edit]` prompt into a verdict. None of
  those were changed, because a lab whose green nobody has seen is the failure
  this project keeps paying for.

* **The self-test coverage reader could be satisfied by the wrong message.** It
  took each call site's longest literal chunk as a needle and asked whether any
  printed line contained it. Measured on this tree: 7 of 113 needles are
  substrings of another message's, and two pairs of call sites have needles
  that are byte-identical - `statistics segment ` belongs to both the
  75%-full refusal and the healthy report, and `records mirrored on ` to both
  mirror lines. A reader keyed on the longest chunk alone cannot tell those
  apart, so it either misses a branch that never ran or accuses one that did.
  It now matches **every** literal fragment of a message, in order, and spends
  each printed line on the most specific message it satisfies. Getting there
  cost two wrong versions, both caught by running it: cutting the message at
  the first quote before punching out the values truncates any message
  containing a `$(...)`, and cutting it afterwards is required because a `case`
  arm is `ok "..." ;;` and the trailing `;;` otherwise becomes a fragment the
  reader demands of a printed line.


* **`make test` counted 59 suites over a tree holding 61 harnesses, and named
  neither of the two it was not reading**
  ([#338](https://github.com/andrediashexa/hexarouting/issues/338)).
  `appliance/test_startup_conf.sh` and `appliance/test_tuned_conf.sh` are not
  in `test/suites.list`. They run only from `make test-appliance`, which is a
  docker target, so `make test` never read them - and `59 suites: 59 passed` is
  the count of what the list names while a reader reasonably takes it for the
  count of what this repository measures. That is the shape behind
  [#335](https://github.com/andrediashexa/hexarouting/issues/335) with a bigger
  radius: not one branch unmeasured, but everything two whole files assert
  about the installed tuner, the generated `startup.conf` and the VPP that has
  to come up with it.

  `tools/run-tests.sh` now ends every run by saying how many harness files are
  in the tree, how many of them it read, and which ones it did not. Discovered,
  not listed: a harness is a file whose name begins with `test_`, which is the
  convention the repository already follows, so a third one added tomorrow is
  named on the next run by nobody's decision. A hand-kept roster of excused
  files would be a second thing to keep true and the next person does not
  update it, which is how these two stayed invisible. The one indirection is
  the compiled suites - `.build/test_bpa` carries nothing of the name
  `test/test_bpa_pool.c` - so the Makefile is asked which sources build a
  target the list names rather than assuming the two names agree.

  **Neither harness can join the list, and this is what each needs a container
  for.** `appliance/test_tuned_conf.sh` stops at its line 17, which requires
  `/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages` to be writable: on
  an unprivileged box it prints `SKIP:` and exits 0 with no verdict line at
  all, so in the list it would be NO VERDICT on every run rather than a
  measurement. Its line 64 then starts a real `vpp` with the tuned file, which
  is the only reason the file exists at all: `appliance/test_tune.py` already
  asserts the same rendered profile against synthetic roots, so what is left
  once VPP is taken away is already measured next door.
  `appliance/test_startup_conf.sh` produced 15 FAILURES when run on this box,
  every one of them for the absent product rather than for a defect: its line
  13 rebuilds VPP in `/src/.build-vpp`, its line 14 installs into the real
  `/usr/lib/hexarouting` and runs the binaries from there, and its line 111
  starts `vpp -c /etc/vpp/startup.conf`. Weakening either one to make it
  countable would be a check that stopped checking in order to be counted.

  `test/test_run_tests.sh` holds eight assertions on the new report. Six are
  driven against an arranged tree, where the answer is known because that file
  wrote it: a harness the list does not name is named, one it does name is not
  accused, the source of a binary the list names is not accused, a leftover
  binary in `.build` is not mistaken for a harness, the two numbers are the two
  numbers, and a run that read every harness says so rather than going silent.
  Two are against the real tree: whatever the report names must exist and must
  really not be in `suites.list`, because a report that accuses a green suite
  is one people learn to skip; and the discovery must find at least the
  harnesses `suites.list` itself names, because a `find` with a bad prune
  returns nothing and "0 harness files in the tree" reads exactly like a
  repository with none.

* **`docs/appliance.md` still said the commit binds the NIC**
  ([#336](https://github.com/andrediashexa/hexarouting/issues/336)). That
  sentence described the defect, accurately, right up until the defect was
  fixed - and then it was the document claiming something the box no longer
  does. Found by the sweep of `src/hexash/hexash.py` for sentences printed by a
  path that never asked which box it was on, which noticed it in the
  documentation rather than in the code and left it alone as another branch's
  territory. It was.


* **Two more screens stated a consequence without asking which box they were
  on.** Both found by sweeping `src/hexash/hexash.py` for the shape behind
  [#328](https://github.com/andrediashexa/hexarouting/issues/328),
  [#330](https://github.com/andrediashexa/hexarouting/issues/330),
  [#331](https://github.com/andrediashexa/hexarouting/issues/331) and
  [#336](https://github.com/andrediashexa/hexarouting/issues/336): a sentence
  true of one box and false of another, printed by a path that never asked.

  **`commit` told a box with a spare card that handing it over would end the
  session typing the command.** *"restarting it hands the card over and ends
  every session on this box, including this one, so it is not done for you"*
  was printed on every box that named a card. It is true of the box the whole
  handover path was written for - the one whose only card carries the
  management address - and false of the two-card box that is the worked
  example in `docs/appliance.md` and in the operator guide, where the session
  arrives on a card Linux keeps and a dataplane restart never touches it. An
  operator with a card to spare was told the safe change was the dangerous
  one. The product already answers that question correctly in three other
  places - the refusal offers the other card, the management record is written
  only for the card carrying the route, and `commit` arms a confirmed commit
  only when the card carries the session - and this was the fourth, which did
  not ask. It now reads the same pair `show hardware` marks the management
  card from, and answers four ways: this card is the path the box is reached
  on, which keeps the old sentence unchanged; the path is another card Linux
  keeps, and is named; the path is on something that is not one of this box's
  cards, which on a box that has already handed one over is the `linux_cp`
  pair the dataplane itself holds, so the session may go with it; and nothing
  carries a default route, so it cannot be read. The last two take the loud
  side, for the reason #123 gives: a box warned about a session it was never
  going to lose spends a maintenance window it did not need, and a box told
  the session was safe and losing it is a box nobody can reach.

  **`show hardware` pointed at lines saying the opposite of what it claimed
  they said.** With no PCI NIC in Linux the screen offered the reader a
  choice - *"Either the dataplane already owns every one of them, which the
  lines above say, or this box genuinely has no card Linux can see"* - and
  three different boxes reach it. On one the lines above do say that. On the
  second they have just said the dataplane holds nothing, and on the third
  that it could not be asked at all, so the screen contradicted itself six
  lines apart and then asked the operator to settle a question it had already
  answered. That is #331's shape exactly, where the PCI-address line read
  `(not a PCI device)` two lines under a line saying no card had been named.
  Three boxes, three sentences, and the one the old wording was right for
  keeps its answer. The virtio sentence #328 added is true of all three and is
  printed on all three.

  Shown red on four mutations of the product, not on a reworded check: the
  empty-table sentence made one sentence again (three red, one per box), the
  virtio sentence confined to one branch (three red), the restart sentence
  made unconditional again (five red, which is the defect as it stood), and
  the same sentence made unconditionally quiet, which takes it from the box it
  was written for (one red).

  `wiki/comecar-aqui/primeiros-passos.md` and `docs/appliance.md` both quoted
  the old transcript for the two-card box, which is the box it was wrong on,
  and carry the new one.

* **A box whose FRR was silent was told what its BGP neighbour keeps.**
  `show bgp neighbor <n> received-routes` and `clear bgp <n> soft in` both need
  the neighbour to be holding what it sent, and both refuse when it is not.
  What decided that was `Frr.running()`, which reports a control plane that is
  **down** as a configuration with nothing in it - the exact collapse
  `Frr.daemons()` forbids forty lines above it in the same class: *"None is not
  the empty set and the two MUST NOT be collapsed."* So a box whose vtysh was
  not answering got a refusal about its neighbour's configuration, read off
  nothing, with three pieces of advice none of which it could follow: add a
  statement the box may already carry, through a daemon that is not there to
  take it; then `show bgp neighbor <n> routes`, which answers from the same
  silent FRR; then `clear bgp <n>`, an outage offered on a premise nothing
  read, on a box where it would reset nothing. The reading answers three ways
  now, and the two screens say which. Neither path had a single assertion
  before this - the same gap #328 found in `which_dpdk_driver()` - and both
  have one on each of the three boxes, shown red on the collapse being put back
  (five red) and on "cannot ask" being read as "it keeps everything", which
  silently drops the refusal the box with no soft-reconfiguration needs (two
  red).

* **The roadmap disagreed with the tracker, and the tool that measures that had
  no caller.** `docs/roadmap.md` is what somebody reads to decide what to do
  next, and it presented a closed issue as work that remains while never
  mentioning two open ones. `tools/roadmap-state.sh` has been able to say so
  since it was written and nothing ran it: it needs the network to know an
  issue's state, so it cannot live in `make test`, and a reader nobody calls is
  a rule that has quietly stopped applying - which is exactly what
  `test/test_selftest.sh` refuses of the verdict holder's readers. The release
  gate asks it now, beside the wiki check, at the moment somebody is deciding
  the tree is worth a version number. It degrades loudly when `gh` cannot
  answer, because a release refused because GitHub was unreachable is a gate
  people learn to argue with.

* **A document check sat behind one that had been red since September.** The
  wiki comparison refused on the spot, so the roadmap comparison placed after it
  would never have executed once - the wiki has been behind since 2026-09-09.
  Both verdicts are taken before either refusal now, so a maintainer learns
  everything that is wrong in one run rather than fixing the wiki, running
  again, and discovering the roadmap. The assertion requires the gate to have
  **asked**: a gate that stopped calling the holder would still print "the
  roadmap and the tracker agree", and that reads as a pass.


* **The handover lab asserted a design this product does not have**
  ([#332](https://github.com/andrediashexa/hexarouting/issues/332)). PHASE 5
  required that Linux no longer had the card's name after the handover, and
  that the management address had moved to some other interface. Both are
  wrong. The promise is that the **name survives**: the dataplane takes the PCI
  device, `linux_cp` makes a TAP carrying the same name, and the address goes
  back onto it, so nothing across the cable re-learns anything. Measured on a
  booted guest with the fixes in: the card bound to `uio_pci_generic`, the
  dataplane holding `GigabitEthernet0/9/0` **and** `tap4096`, and `ens9`
  present with `10.0.2.15/24` on it - which the lab called a failure twice.
  What actually distinguishes a successful handover from one that never
  happened is what that name resolves to: a TAP has no PCI parent and the card
  did. The checks read that instead, and the address check is now conditioned
  on it, because "the address is on `ens9`" means opposite things depending on
  what `ens9` is.


* **The lab's console driver could only wait for success, and had never been
  tested** ([#332](https://github.com/andrediashexa/hexarouting/issues/332)).
  `lab/install-iso.sh` types at a serial console and waits for strings the box
  prints, and every one of those waits had only ever been exercised by booting
  a machine - which costs minutes and cannot arrange the case where the box
  answers something else. It cost exactly that: PHASE 4 spent its full 600
  seconds waiting for `commit complete` on a commit that had printed `commit
  failed` seconds in, and what diagnosed it was the console dump the timeout
  prints rather than the wait. Ten minutes per run, and a verdict reading "the
  box never answered" about a box that answered immediately. `wait_for_either`
  takes both strings and reports which arrived: 0 for the first, 2 for the
  second, 1 for neither, so a caller that ignores the distinction still reads a
  refusal as a failure rather than as a pass. `test/test_console_driver.sh`
  measures those waits **without qemu**, reading the functions out of the lab
  rather than copying them, because a second copy of a wait would pass while
  the real one did whatever it does.


* **Swept every harness for the stub that answers a constant, and found seven
  self-test branches, a lockout and two suites that were red on the machines
  this product ships to.** Four times now a test stub has answered the same
  thing on every box a suite arranges, leaving the branch that handles the
  other answer unreachable - twice in one `systemctl` stub
  ([#222](https://github.com/andrediashexa/hexarouting/issues/222)), once in
  `vat2` ([#335](https://github.com/andrediashexa/hexarouting/issues/335)) and
  once as a stub that was simply missing
  ([#336](https://github.com/andrediashexa/hexarouting/issues/336)). Each was
  found by hand, one at a time. All 50 harnesses have now been read and driven
  under a PATH that records every command reaching the machine underneath: 48
  stubs, and five suites answering questions about a box from whichever
  machine ran them.

  Three branches of `appliance/selftest` reported a broken box as a healthy
  one and no box had ever run them. A VTY that is installed and dies at start -
  the defect 0.2.0 shipped, a `hexash` that died for want of `config.py` -
  printed `[ ok ] VTY starts: Traceback (most recent call last):`, because the
  stub answered one constant line. A box in FIRST BOOT mode, running unpinned
  with no hugepages at a fraction of the throughput it will have after the
  reboot, only ever reached `[ ok ] full tuning active`. A records volume
  naming an array `/proc/mdstat` does not describe only ever reached
  `[ ok ] records mirrored on 2 disks ()`, counted off a block belonging to a
  different array. Four more are the ways a manifest can fail to answer for
  itself ([#302](https://github.com/andrediashexa/hexarouting/issues/302)).
  All seven have a box now, and `journalctl` - the one command the self-test
  reaches for that nothing stubbed - answers from the fake box rather than
  splicing the journal of whatever machine ran the suite into the report the
  80-column check reads.

  `appliance/hexash-login` read the VTY at `/usr/bin/hexash`, written out, so
  nothing could arrange a box that HAS one: every check ran against the
  absence of that file on the machine running the suite, and the fallback that
  drops an operator into a shell when the VTY dies - the only reason the
  wrapper exists instead of pointing root's login shell straight at `hexash` -
  had never been executed anywhere. `HEXA_HEXASH` is the seam, and three boxes
  are arranged where there was one.

  Two suites were green here and red on the boxes this product runs on.
  `test/test_installer.sh` names the machine's own first block device as its
  install target and nothing stubbed `findmnt`, so on a live-booted machine -
  the ISO this product ships - the installer refuses every case as the medium
  it booted from: **34 failures**, over a product nobody changed. Nothing
  stubbed `ip` either, so the card an unattended install takes came from the
  machine's routing table; the suite passed only because the box these
  fixtures were written on routes out of a card called `ens18`, and the
  fallback beside it - "no default route, so take the first card", which is
  the one-NIC box
  [#303](https://github.com/andrediashexa/hexarouting/issues/303) is about -
  had never run. And `appliance/test_tune.py` drives the real `apply-tuning`
  with no `HEXA_ROOT` and no binder stub: measured with a recording binder
  installed, **five real bind attempts in one run** - on an appliance running
  its own suite, five attempts to take its dataplane card off the kernel
  driver.

  The class is closed rather than the instances. `test/test_selftest.sh` now
  carries a second reader beside the one
  [#335](https://github.com/andrediashexa/hexarouting/issues/335) added. That
  one refuses an `[ ok ]` line printed by every box, which is a check whose red
  form nothing has seen; this one refuses a message printed by no box, which is
  a whole branch nothing has run. The `hexash` constant showed up only as the
  second, because the line it hid behind was printed by a handful of boxes and
  not by all of them. The call sites are read out of `appliance/selftest`
  itself, so there is no list to keep up to date and no exception in the rule:

  ```
  [ ok ] 113 messages appliance/selftest can print, and a box for every one
  ```

  The count is read out of the product, so it moves with the product: that
  line reads **118 messages** on this tree, which is what
  `sh test/test_selftest.sh` prints today. 113 is the number the rule was
  closed at.


* **`commit` bound the card immediately while telling the operator it had
  not** ([#336](https://github.com/andrediashexa/hexarouting/issues/336)).
  Naming the dataplane card restarts `hexarouting-tune`, that unit runs
  `appliance/apply-tuning`, and `apply-tuning` performs the bind. At boot that
  is right: the tuner has chosen the device and VPP is ordered after it, so the
  card goes straight to the process about to take it. Restarted from a commit
  it is not - VPP is already running and is not restarted, so the card comes
  off its kernel driver with nothing to pick it up and belongs to neither
  stack. On a box whose only card carries the management address that is the
  operator losing the box, while the VTY prints in the same breath *"NOT IN
  EFFECT YET: the dataplane claims its card when it starts, and this commit
  does not restart it"*. Measured on a booted guest: the driver read
  `uio_pci_generic` and the netdev was gone three lines under that sentence.
  The bind now runs only when the dataplane is positively known **not** to be
  up, and the three boxes are three answers: running, not running, and nobody
  here can be asked - which is a chroot during an install or a container, not
  an appliance starting, and where a bind takes a card off whichever machine is
  really running. Skipping one that should have happened leaves a box that
  forwards nothing and is reachable; performing one that should not leaves a
  box nobody can reach, and this file already carries that trade in
  [#123](https://github.com/andrediashexa/hexarouting/issues/123)'s refusal.


* **The self-test asked only half of "who needs this pair", and the half it
  asked is false on the box that most needs the answer**
  ([#334](https://github.com/andrediashexa/hexarouting/issues/334)). A
  dataplane interface with no `linux_cp` pair was reported loudly or quietly
  depending on one question put to FRR: does `show running-config` carry a
  `^router ` stanza. The premise is sound as far as it goes - a pair is what
  FRR sees, so a box running no protocol is not obviously broken without one -
  but it stopped being the whole question when the pair became the thing that
  carries the **management address** after a handover. A box that gives its
  only card to the dataplane takes a static default route and has no `router`
  stanza at all, so the one box where a missing pair means nobody can reach it
  was told so as `[warn] no linux_cp pair on:` - the tone used for a missing
  nicety, on a box reachable at a console somebody has to walk to. Found while
  fixing [#332](https://github.com/andrediashexa/hexarouting/issues/332): the
  qemu guest in [#329](https://github.com/andrediashexa/hexarouting/issues/329)
  printed the hard form only because something else in that lab's session had
  left a `router` stanza behind.

  The check asks both questions now. The handover record the tuner holds -
  `HEXA_DATAPLANE_MGMT` in `/etc/hexarouting/tune.env`, written by the VTY
  beside the card's name and read back here the way `bind-dataplane-nic` reads
  it - says this box handed its management card over and therefore needs the
  pair whatever FRR is doing; the `^router ` stanza says FRR needs it. Both are
  facts the box has. A failing box leads with the worse of the two reasons,
  because a box nobody can log in to is not a box whose adjacencies are down,
  and it prints the management path the record holds so that the operator at
  that console has the address to put back by hand. A box with **neither** - no
  pair, no handover record, no routing protocol - stays a warning, which is the
  point of asking two questions rather than failing every unpaired interface: a
  box that forwards CGNAT and runs no protocol is a supported configuration of
  this product, and a check that goes red on it is one people learn to run
  around.

  All three states are measured, and each assertion was shown red on a mutation
  that runs and gets it wrong rather than one that crashes: the check asking
  only `^router ` again (the defect as it shipped - the handover box falls back
  to the warning, with no address in it), asking only the handover record (the
  routing box falls back to the warning), failing every unpaired box (the
  correct box goes red, and is told routing is configured on a box that has
  none), the adjacency prose said unconditionally, the routing half swallowed
  by the handover branch, and the headline given to the lesser reason on a box
  that has both.

* **A self-test check that no box had ever seen fail, and the reader that
  refuses the next one**
  ([#335](https://github.com/andrediashexa/hexarouting/issues/335)). Measured
  over the 122 boxes `test/test_selftest.sh` arranges: the `vat2` stub answered
  the constant `bpa_pool_add_del`, so `binary API NOT registered: no CGNAT pool
  can be configured here` had never run anywhere. That is the check written for
  [#119](https://github.com/andrediashexa/hexarouting/issues/119) - the medium
  shipped without the vat2 plugin for the life of the project, and such a box
  boots, routes, answers ssh and fails every pool commit, which is the one
  thing it exists to do. It is the check you most want to be able to go red.
  The stub now answers from the fake box, the way `systemctl` already does -
  and the comments on that stub record the same repair being made twice before
  ([#222](https://github.com/andrediashexa/hexarouting/issues/222)), one
  constant at a time, which is how a third came to be sitting three lines
  below them. So the class is closed as well as the instance: **no `[ ok ]`
  line may be identical across every box the suite arranges**, measured from
  the runs rather than from the source, because a stub that answers a constant
  looks like any other stub and what gives it away is a hundred boxes all being
  told the same thing. The rule carries no exceptions. When it went red on the
  manifest line - whose red form is measured next door in
  `test/test_manifest.sh` - the repair was to arrange that box here too, not to
  excuse the line, because an excused line is one nobody re-examines.


* **The cores table an operator sizes hardware from had no reader**
  ([#172](https://github.com/andrediashexa/hexarouting/issues/172)). The public
  page somebody reads before choosing a processor carries a table of how many
  cores forward and how many are left for everything else, and that is a claim
  about what `appliance/hexarouting-tune` does. Nothing compared the two, so the
  day the split changed the page would have gone on selling the old one - to a
  reader who finds out after the box is racked. The tuner is now the holder and
  the page the reader: every row is driven against the tuner on a synthetic
  machine of that shape, and a row that has left the page in a shape the check
  can read is a failure rather than a silent pass. Shown red from both sides -
  a tuner that leaves two cores to the control plane on large boxes only, which
  the existing worker checks do not see, and a page promising 30 forwarding
  cores on a 32-core processor.


* **A dynamic pool put one subscriber on two public addresses, while the box
  documented paired address pooling in four places**
  ([#226](https://github.com/andrediashexa/hexarouting/issues/226)). RFC 6888
  REQ-2 makes "Paired" (RFC 4787 REQ-2) the required **default** for a CGN.
  This box stated it in `docs/roadmap.md`'s conformance table, in
  `docs/architecture.md`, on `bpa_pool.h`'s static-forward comment and on the
  public wiki page about port forwarding, and the forward path REFUSES an
  operator-chosen public address because of it. It was true of deterministic
  mode and false of dynamic mode: an extension was whatever came off the free
  stack, and under `policy interleaved` the next block index is the next public
  address, so a subscriber that filled its block and was given another normally
  ended up sending from two addresses at once - more often the larger the pool,
  because alignment needs the gap to be a multiple of the address count. The
  claim was narrowed in an earlier release. The mechanism is what changes here.

  **The allocator now prefers a free block on the subscriber's own public
  address.** Two bounded walks along that address, in steps of
  `n_workers / gcd(unit, n_workers)` - outwards from the subscriber's own
  block, whose neighbour is the range an investigator reads as one holding,
  and upwards from wherever the free stack is about to hand blocks out, which
  is where a pool that filled from the bottom keeps its free ones. A
  **preference and not a rule**: it falls back to the free stack rather than
  refusing, because refusing would turn a property about layout into a
  customer losing traffic.

  So the honest statement is a rate, not a guarantee, and both the box and the
  documentation now state it as one. Measured over three arrival patterns -
  one subscriber at a time, everybody-arrives-then-everybody-extends, and a
  steady state with three quarters of the subscribers reaped - across 1 to 256
  public addresses and 1 to 8 workers:

  | arrivals | before | after |
  |---|---|---|
  | one at a time | 0.0% | **100%** |
  | everybody, then everybody | 0.0% | **99.6%** |
  | steady state with churn | 0.2% | **99.2%** |

  Those are the lowest cell of each grid, not the mean. `show bpa blocks`
  prints each pool's own count, because an operator sizing or selling against
  a conformance statement needs their number rather than the sentence:

  ```
  RFC 4787 REQ-2 paired: 412 of 419 extension(s) on the holder's own address
  ```

  **One correction to what this issue recorded about the cost.** Pairing does
  **not** inherit the block stride's reachability limit
  ([#55](https://github.com/andrediashexa/hexarouting/issues/55)). A stride has
  to honour the one number the operator typed, so `stride x unit` must be a
  multiple of the worker count; pairing may take **any** multiple of `unit` and
  needs only the smallest that lands in the slice, which always exists. The
  vectors say how far apart those are: of the 72 (pool, worker count) shapes
  `ref/gen_vectors.py` now generates, **53 are shapes where a stride of 1 is
  unreachable and pairing still has a step**. A box that cannot be strided can
  still be paired.

  **The free-list removal is now constant time**, which is what made the
  preference affordable. Taking a NAMED block off the free stack used to be a
  search with a guess in front of it, and the guess only held while nothing had
  displaced the stack - true on the path that pins many blocks at once, false
  on any path that names blocks in an order of its own. Measured on a
  16-address pool at 3 workers with the preference on: **72684 free-list
  entries examined for one extension**, 30 on average. `free_pos[]` is the
  inverse index, four bytes a block - a 258048-block slice grows from about
  4.4 MB to 5.4 MB - and it removes the walk from the pin path too, which the
  old comment measured at about 0.8 ms per pin.

  **`test/test_bpa_dyn.c` is new, and is the first suite in `make test` that
  drives this allocator at all.** Until now the dynamic half of REQ-2 was
  asserted only behind docker, and by `lab/crossaddr.sh`, which was written to
  pin what the box did WRONG. `test/bpa_vec_shim.h` reproduces the VPP vector
  and hash operations the allocator's behaviour depends on - `vec_del1` swaps
  with the last entry, `vec_pop` is a stack - so the real `bpa_dyn.c` runs with
  no VPP and no container. Two things it found while being written, both of
  them checks that could not fail: a four-subscriber scenario on four public
  addresses stays green with the preference deleted, because `4 % 4 == 0` puts
  every extension back by accident, and "the block is in this worker's slice"
  is true of `local * n_workers + worker` whatever the allocator computed. Both
  are rewritten to assert what a wrong answer actually produces, and each check
  carries the guard that makes it able to go red.

  **A deterministic pool's reserve is counted the same way, and is now
  asserted without a container.** Its extensions come out of the reserve, which
  has the same preference and the same fall-back, so one counter answers the
  property for both allocators. The only assertion on it was
  `lab/detreserve.sh`, and the scenario there runs four subscribers over four
  public addresses in descending order against an ascending free stack, which
  is what stops the answer being true by accident - the same scenario now runs
  in `make test`.

  **`lab/crossaddr.sh` is now red by construction and is left that way.** Every
  `expect` in it asserts the behaviour this change replaced - which is what its
  header said would happen. Its assertions are numbers measured off a running
  box and nobody has run it since, so they are not rewritten from arithmetic:
  doing that would put a lab in the tree whose green nobody has seen.
  `tools/run-all-labs.sh` says so where it lists what it is not running, and
  re-running it against a box is the work left on the issue.

* **The capacity a subscriber can actually reach was printed on one interface
  and not on the other, and no check outside a container could see the number
  at all**
  ([#210](https://github.com/andrediashexa/hexarouting/issues/210)).
  `show bpa pool` stopped claiming a ceiling it cannot deliver when a dynamic
  pool's blocks are sharded per worker. `bpa_pool_details`, which is the
  interface a provisioning system reads, went on reporting `capacity` alone -
  the same false promise, on the mouth that answers a machine rather than
  somebody who might notice the second line. It now carries `guaranteed` and
  the `n_workers` it was computed on, because the floor is not a property of
  the pool: the same configuration guarantees a different number on a box with
  four workers, and a caller that stored the figure without it could not tell
  a re-sized box from a changed pool.

  The arithmetic moved to `bpa_pool_guaranteed()` in
  `src/plugins/bpa/bpa_pool.h`, with `bpa_pool_usable()` beside it, and it is
  now one holder with three readers instead of a copy per reader - there were
  already two of `usable`, in `bpa_cli.c` and in `bpa_pool_remap_count()`.
  Moving it is what makes it reachable: it was `static` inside `bpa_cli.c`,
  which builds only against a VPP tree, so the central capacity claim of this
  product was asserted by `lab/dynamic.sh` and by nothing else. `make test`
  could not see it, and a regression in it would have shipped with 56 suites
  green.

  `test/test_bpa_pool.c` now checks it three ways, and the third is what #210
  asked a lab for. Twelve sized cases, the measured pool among them - three
  blocks in front of a `/22` at two workers promises **1**, not the 3 the box
  used to print - and the five rows of the sizing table the wiki shows an
  operator. Then the nine pool definitions the vectors carry, where the floor
  must equal the ceiling in deterministic mode and at one worker, since a
  caveat shown where it does not apply is one people learn to skip. Then 672
  pools swept, 270 of them losing something to the shard, each one checked
  against a **simulation of the mechanism** rather than against the formula
  written a second time: `bpa_dyn_init()` cuts the slices, `bpa_owner_worker()`
  decides which slice a subscriber may draw from, and every way n subscribers
  can fall across the workers is walked. Every subscriber the pool claims
  capacity for gets a block, and one more than that can be refused - the
  second half matters as much, because a floor that is merely never exceeded
  sends an operator to buy addresses they already own.

  **Measured, and it settles half of what #210 left open.** Over 2800
  combinations of blocks, inside addresses and worker count, there is **no**
  way of splitting the blocks among the workers that guarantees more than the
  even split already does. Re-partitioning is not a cheap fix hiding behind
  the expensive ones: with a subscriber's worker fixed by arithmetic on its
  own address, the adversary crowds whichever slice is thinnest, so the floor
  is `capacity / workers` for any static partition. The two options that do
  recover the loss both need shared state, which is what the partition exists
  to avoid, and remain the maintainer's decision.

* **A check that asserted about a word in prose went red on work that was
  right** ([#333](https://github.com/andrediashexa/hexarouting/issues/333)).
  `test/test_kernel_tuning.py` asserts that none of the kernel parameters
  `docs/appliance.md` lists as deliberately NOT applied is set by the
  appliance, and it looked for the bare parameter name anywhere in any of the
  32 files under `appliance/`. Three of the seven candidates are ordinary
  English: `audit`, `tsc` and `clocksource`. Writing the word "audit" in an
  English sentence in a comment during the
  [#248](https://github.com/andrediashexa/hexarouting/issues/248) work turned
  the check red on a comment that set nothing, and the comment was reworded to
  get past it. Measured on the current tree by putting one such comment back:
  three FAILs, all three false, none of them touching a kernel command line.
  A check that can go red on correct work is one that whoever is holding the
  branch reroutes around, by rewording, by an exception, and eventually by
  deleting it.

  It now asserts about a parameter where a parameter lives. The generated
  command line is parsed with the same `params()` the applied table is
  compared against, so a token is a parameter there or it is not. In a shipped
  file a valued candidate must appear as `name=`, the `=` being what turns a
  word into a parameter, or as a `sysctl.conf` assignment at the head of a
  line; a bare flag such as `nosoftlockup` carries no value anywhere, so the
  whole token is the parameter. `#` comments are cut first, quote-aware,
  because a comment is prose about a parameter and never a parameter: a `#`
  line boots nothing in a shell script, a systemd unit, `sysctl.conf` or the
  `grub.cfg` the medium is built with. There is no allowlist of excused words,
  which is the thing the next person would have grown.

  **It catches more than it did, not less.** Each half was shown red on a real
  setting: `audit=0` added to the string `hexarouting-tune` builds
  `GRUB_CMDLINE_LINUX_DEFAULT` from is caught twice, once by the parsed line
  and once at `appliance/hexarouting-tune:554`; `nosoftlockup` added to the
  boot line `build-iso.sh` writes into the medium is caught at
  `appliance/build-iso.sh:314`, where the parsed line cannot see it because no
  synthetic topology renders it. And `kernel.nmi_watchdog = 0` in
  `appliance/etc/sysctl.conf` is caught at line 36 - the old sweep was blind
  to it, because its lookbehind refused a `.` in front of the name, so the one
  file the check was first written to read was the one shape it missed. The
  same comment that produced three false FAILs before produces none now.

  Two of those three line numbers were true of the tree that day and are not
  true of this one. The FAIL names the file and the line it read the parameter
  on, so re-driving the same three mutations here reads
  `appliance/build-iso.sh:314` unchanged, `appliance/hexarouting-tune:709` -
  that file has grown 792 lines since - and `appliance/etc/sysctl.conf:34`,
  which is the line an appended `kernel.nmi_watchdog = 0` lands on in a
  33-line file. There is no line 36 in it and there was none then either.

* **A box that handed its only card to the dataplane came back with no address
  on any interface and nobody could reach it**
  ([#332](https://github.com/andrediashexa/hexarouting/issues/332)). Measured
  on a booted qemu guest by the new phases above, against the published 0.74.2
  medium. The box did everything the VTY promised except the last clause: the
  bind worked - `virtio-pci` to `uio_pci_generic` - Linux released the netdev,
  the dataplane held `GigabitEthernet0/9/0`, and the box answered nowhere. Its
  own boot self-test printed the reason on the same console: routing is
  configured and no interface is paired into the kernel. So the diagnosis was
  right and the doing was missing. On a single-card machine that means
  somebody walks to it, which is the outcome the handover exists to prevent.

  **The two halves of one decision had two lifetimes.** `commit` writes the
  card and the management path it carries into the tuner's file, and that file
  is what performs the handover at the next boot. The pair the address goes
  on is made by `hexash --load`, out of `/etc/hexarouting/hexarouting.conf` -
  and `commit` does not write that file. Only `save` does. So the boot after
  the handover replayed a file that had never heard of the card: on a fresh
  appliance the three comments the installer ships, which `Config.is_empty()`
  returns on before `apply()` is reached, so `apply_management_path()` was
  never called at all. The half that hands the card over survived the reboot;
  the half that keeps the box reachable did not. `load()` now takes the card
  and its record from the tuner's file - their holder, and the reason the
  record is kept there at all - when the file being replayed carries neither,
  and puts the management path back even on an empty file, because a file with
  no statements in it is not a box that gave its card away. A box that HAD
  saved before the handover was worse off still: its file said nothing about a
  dataplane card, so the boot replay read that as a card being taken away and
  un-named the card the box was holding, and the next boot gave it back.

  **And a card the dataplane has not got is now two answers instead of one.**
  `apply_management_path()` returned success, silently, whenever the recorded
  MAC was not among the dataplane's interfaces. That is right on the boots it
  was written for - `hexash --load` runs before the operator reboots into the
  handover, and after a rollback has given the card back, and a commit that
  failed because a card had not moved yet would be a box that cannot be
  configured at all - and it is wrong on the boot where the card is gone from
  Linux too. There the card is on neither side, the address has nowhere to go,
  and the box is off the network, which is the one state where the console is
  the only place left that can be told. The box now asks which boot it is on,
  by looking for the MAC under `/sys/class/net` - a device DPDK has taken is
  not there at all, and the Linux name is the one thing that does not survive
  the handover - and says so rather than returning success:

  ```
  error: the card this box is reached on was handed over and nothing took it
      Linux no longer has ens18 and no interface the dataplane holds has
      52:54:00:00:00:01, so there is no pair for 10.95.200.198/24 to go on
  ```

  "Cannot look" is not "the card is gone": a tree with no `/sys/class/net` in
  it - a developer's box, this VTY's own suites - answers that the card is
  still Linux's, the same posture `dataplane_if_refusal()` already takes about
  a card list it could not read. Each of the five assertions in
  `test/test_dataplane_if.py` was shown red on its own mutation of the
  product, and the boot cases are driven through `load()` against a file on
  disk rather than by calling the function directly, because "never called"
  is the defect and a case that calls it cannot see that.

  **What this does not prove.** The guest itself. Everything here is measured
  against the product's own seams in a tempdir; that the box comes back and
  answers on the network needs `lab/install-iso.sh` PHASE 4/5 on a booted
  guest, and nothing in this entry claims it has been run again.

* **A box installed from a medium that carried SSH host keys said so only to
  its console, at boot**
  ([#248](https://github.com/andrediashexa/hexarouting/issues/248)). Every
  medium published up to and including 0.66.0 carried the host keys its build
  container generated, private halves included, so every box installed from
  one of them answers with the same key and the private half of it is in the
  published download: an SSH session to such a box can be impersonated by
  anybody holding the medium, and the operator's client does not object,
  because the fingerprint it pinned is the fingerprint it was handed.
  [#245](https://github.com/andrediashexa/hexarouting/issues/245) fixed the
  image and the installer, which fixes boxes installed from then on and
  nothing already racked; the boot self-test learned to recognise a
  handed-down key and say so. That report is written to `/dev/console`. A
  racked appliance is reached over SSH, so nobody is attached to that console,
  and the report is not recoverable afterwards either - the self-test falls
  back to stdout only when the write to the console fails, so on a box whose
  console write succeeds `journalctl -u hexarouting-selftest` has nothing. The
  operator whose session can be sat in the middle of is the one logged in over
  it, and that operator had no way to be told.

  The VTY banner now says it, at every login, on a box that is affected and on
  no other: it names the key files, says the private half is in the published
  download and that this session can be impersonated with nothing to notice,
  gives the three commands that regenerate a set, and warns that every client
  pinning the old fingerprint will refuse the next connection - which is the
  repair happening rather than an attack.

  **A box that is not affected is told nothing.** The answer is measured per
  login rather than remembered from the boot before it, so a box remediated by
  hand stops being warned at the next login with no reboot, and a box whose
  keys are its own never sees the line at all. The signal is that a file
  cannot have contents older than itself: `unsquashfs` restores the build
  container's mtime onto an inode the installer creates, and a key generated
  here cannot predate its own file. Neither the key's comment nor a list of
  published fingerprints is read - the first names the live medium's hostname
  on a correctly installed box, and the second would be key material back in
  the tree, which is what #245 removed.

  The rule, the key names and the commands live in one new file,
  `appliance/ssh-host-key-origin`, which both the boot report and the banner
  render. A repair printed on two screens out of two files is a repair that
  can be corrected in one of them; `test/test_ssh_host_key_origin.py` refuses
  a reader that spells it, and the self-test now fails rather than going quiet
  when that file cannot be run, because a check that could not run reads
  exactly like a check that agreed.

* **The tuner told a box with no dataplane card that its card is "(not a PCI
  device)"** ([#331](https://github.com/andrediashexa/hexarouting/issues/331)).
  One line covered two states that are nothing alike: no card named at all,
  which is every fresh install, and a named card whose PCI address could not
  be read. The sentence written for the second was printed for the first, two
  lines under a line saying no NIC had been given - so the report contradicted
  itself on one screen, and the reasonable reading sent an operator looking for
  why their card was not PCI. That is the dead end
  [#286](https://github.com/andrediashexa/hexarouting/issues/286) was about,
  reached by a different road. Three states now get three sentences, and the
  one the old wording was right for keeps its answer.


* **The documented upgrade path did not exist on a box anybody had**
  ([#295](https://github.com/andrediashexa/hexarouting/issues/295)).
  `appliance/install.sh` has called itself the path "for updating a box in
  production without reinstalling it" since the first release, and it copies
  every file out of `$REPO`. Measured on a real appliance installed from a
  published medium: there is no `install.sh` on it, no repository and no
  toolchain. So the path served `tools/release-gate.sh` and `lab/upgrade.sh`,
  which have a build tree, and nobody who bought the product - while five
  sentences in the repository and two on the box itself told an operator it was
  theirs. Their only route to a new version was a reinstall, which erases the
  disk and takes `/var/log/hexarouting` with it: a year of connection records
  they remain answerable for, and the configuration trail that makes an answer
  about a past instant defensible. The earlier half of this work corrected
  those five sentences and wrote the operator page; this is the half that makes
  them true again. `appliance/build-upgrade.sh` packs the four directories
  `appliance/build-iso.sh` already installs the medium from, with the two
  compiled plugins laid where `install.sh` looks for them, and
  `appliance/upgrade` runs **that same `install.sh`** out of the unpacked
  result. There is no second implementation of the upgrade: an operator's
  upgrade is byte for byte the one the release gate and `lab/upgrade.sh` have
  been driving all along, which is the only reason it can be believed. Two
  things travel that a tarball cannot work out for itself. The version stamp is
  written into the artifact at build time, because there is no `.git` inside a
  tarball and `install.sh` would otherwise stamp the box "not a git tree" - the
  line `show version` prints and the first thing anybody is asked on the
  telephone. And the VPP and FRR versions `build-iso.sh` pins travel with it,
  read out of that file so there is one owner of them: the artifact carries
  product files and installs no packages, so a release that moves the platform
  is **refused** on the box, naming both versions, before a single byte is
  written. `tools/release-gate.sh` builds the artifact for every release and
  publishes its `sha256` beside the medium's, and `build-upgrade.sh` installs
  what it has just packed into a throwaway root before it will hand it over -
  an artifact nobody installs in the gate is one whose first installation is
  somebody's production box. `test/test_upgrade_artifact.sh` drives the whole
  of it in about a second, against a box that is a directory with a real dpkg
  database in it. What this does **not** do is take a booted box from one
  release to the next: that is `lab/upgrade.sh`'s question and it still runs
  out of a source tree.

* **And the command an operator types is not a path into this product's source tree** ([#295](https://github.com/andrediashexa/hexarouting/issues/295)). The artifact carries `upgrade` at its root, as a symlink to `appliance/upgrade`, so the page an operator reads says `sudo sh upgrade` and nothing on it asks them to know a directory name inside the product. A symlink and not a copy: `appliance/upgrade` resolves the artifact by finding `RELEASE` rather than by counting directories up from itself, so both spellings reach one implementation.

* **`show hardware` told an operator their card was unusable and stopped
  there** ([#328](https://github.com/andrediashexa/hexarouting/issues/328)).
  The driver line read `kernel driver: DPDK cannot take a NIC through it`. It
  is true, it is the state every uninstalled box is in, and on a box with one
  NIC - a virtual machine, which is how most people first meet this product -
  it was the only thing said about the only card. The reasonable reading is
  that the hardware is unsuitable. The screen now says what happens to the
  card instead: the driver it is on, and the driver the box will move it to
  when the dataplane claims it. That second driver is **asked of**
  `appliance/bind-dataplane-nic`, which is where the IOMMU rule already lives,
  through a `--which-driver` mode that answers without binding anything - and
  the rule inside that script, which was written twice, is now one function
  both the answer and the bind call. A box where the binder cannot be reached
  says it could not ask rather than naming a driver that may be wrong.

* **`show hardware` on a box with no PCI NIC said a virtio guest is what that
  looks like** ([#328](https://github.com/andrediashexa/hexarouting/issues/328)).
  False since [#286](https://github.com/andrediashexa/hexarouting/issues/286)
  taught `pci_address_of()` to walk up to the PCI parent: a virtio card in a
  VM resolves and is eligible. The sentence stayed, and the audience most
  likely to reach that screen is exactly the one it was wrong for. A wrong
  sentence on an empty table ends the search, which is worse than the empty
  table.

* **`why no dataplane interface` never named the screen that answers it**
  ([#327](https://github.com/andrediashexa/hexarouting/issues/327)). All three
  branches - hugepages missing, no interface named, a name that did not
  resolve - now point at `show hardware`, which is the command written for
  that exact moment and which an operator had no way to know about.

* **The tuner sized the dataplane's NIC from the processor and never asked the
  card** ([#330](https://github.com/andrediashexa/hexarouting/issues/330)).
  `appliance/hexarouting-tune` derived `num-rx-queues` and `num-tx-queues` from
  the worker count alone - `nq = max(1, len(workers))` - and left the MTU at
  VPP's 9000 default. On an eight-core virtio guest that wrote
  `num-tx-queues 7` and MTU 9000 for a card with one TX queue and `maxmtu
  1500`, DPDK refused the device with `-22` and the box came up owning a NIC it
  could not start: `show interface` listed `local0` and nothing else, on a
  machine whose handover had otherwise worked. The tuner now reads
  `/sys/class/net/<if>/queues` and the card's `maxmtu`, caps the queue count at
  what the card has, and writes an `mtu` line only when the card is below 9000
  - so a jumbo-capable card is left exactly as it was. The report says which
  of those happened, including the case where the queue directory could not be
  read at all: that state still falls back to one queue per worker, which is
  the number that failed, and a report that stayed quiet about it would make a
  box about to refuse its card look identical to a healthy one. A box with no
  dataplane interface says nothing about queues, because a queue count is not a
  fact about a machine that has no card to count them on.

* **A box aborted between 7,000 and 8,000 interfaces, because nothing sized the
  statistics segment** ([#288](https://github.com/andrediashexa/hexarouting/issues/288)).
  `appliance/hexarouting-tune` wrote no `statseg` stanza, so an installed box
  ran on VPP's built-in 32 MB. It is the only resource on the box whose limit is
  an abort rather than a refusal: the run measured for #170 printed `after 7000:
  7000 live` and then died creating the next batch, in `os_panic` and `SIGABRT`,
  taking the whole dataplane. The tuner now counts the interfaces
  `/etc/hexarouting/hexarouting.conf` configures the box for - the file
  `hexarouting.service` hands to `hexash --load` at every boot - and writes a
  stanza sized at **4,793 bytes each**. That figure is the measurement divided
  rather than a constant: 32 MiB over the 7,000 interfaces that segment was
  measured surviving. It is deliberately not the 2.9 kB of counter content
  measured at 40,000 interfaces, which predicts 12,000 on the default and would
  abort at two thirds of what it promised, because the counter directory is one
  contiguous vector that holds two copies while it grows. The count comes from
  **hexash's own parser**, not a grep for `^interface `: the routing half of
  that file names interfaces too, and a second reader of the grammar answers
  the same only until the VTY learns another way to name one. Nothing is
  written below 7,000 interfaces, where the arithmetic asks for less than VPP
  already gives away; nothing is written when a `statseg` is already in
  `/etc/hexarouting/vpp-base.conf`, which is concatenated ahead of the
  generated stanzas and is therefore the operator's last word; and a box with
  no interfaces to size for is told no size at all - no saved configuration, a
  configuration that could not be read, and an install this program cannot
  reach hexash's parser from are three different sentences, none of them a
  number. The stanza is written in the conservative first-boot profile too,
  because `hexash --load` creates those interfaces on the first boot as well.
  **What no configuration file holds is an interface created while the box
  runs** - a PPPoE session, a pair made from the VTY - so a BNG still has to set
  the size itself, and the boot self-test still reads the dataplane's own heap.
  That check gained a third answer for where the size came from: a stanza in
  the base file is the operator's, one that reached `startup.conf` without
  being in the base is the tuner's, and reading only `startup.conf` would have
  told a box the tuner sized that its operator had sized it.

### Added

* **`tools/changelog-state.sh`: the CHANGELOG compared against the tracker.**
  The roadmap has had this since
  [#292](https://github.com/andrediashexa/hexarouting/issues/292) and the
  CHANGELOG never did, so the only thing standing between work shipping and
  work being recorded was whoever was cutting the release remembering. It is
  the same shape and the same argument as `tools/roadmap-state.sh`: a tool and
  not a `make test` suite, because whether an issue is closed is written down
  nowhere in the tree and every suite here passes offline
  ([#317](https://github.com/andrediashexa/hexarouting/issues/317)).

  It asks one question - **is every issue closed as COMPLETED named somewhere in
  this file** - and the three things it deliberately does not ask are written
  beside it with what each was measured against:

  *Named anywhere, not in the section being cut.* An issue is often closed days
  after the release carrying it shipped. Of the 17 closed since v0.74.2 and
  absent from `## Unreleased`, **sixteen** are named in 0.74.1 or 0.74.2.
  Demanding the current section would have reported sixteen findings and taught
  the next reader to skip the list.

  *`NOT_PLANNED` is exempt*, because an issue closed that way is a decision not
  to do something and there is no change to tell anybody about. Two of the 293.

  *An open issue named in the file is not a finding*, which is the opposite of
  what the roadmap tool does. An entry names the issues it argues from and most
  of those are open on purpose. The roadmap is a list of what remains; this is a
  record of what happened, and a check copied between them would be wrong in
  one of the two.

  The truncation guard is the one worth naming. `gh issue list` cuts at
  `--limit` in silence, and a cut answer here is the dangerous direction: every
  issue past it is never looked for, so the tool reports agreement over a list
  it did not read. That is the empty-haystack shape, and it is refused by
  comparing the count returned against the limit rather than by trusting it.

  The gate calls it **beside** the roadmap check and not behind it, which is the
  mistake [#340](https://github.com/andrediashexa/hexarouting/issues/340) is on
  this same file for: a check placed after one that had been red for days never
  ran once.

  **Its own refusals are driven, and the first run of them found the tool's
  worst defect.** `test/test_tool_refusals.sh` gained a section for it beside
  the one for `roadmap-state.sh`, with its own stub because the two read
  different `gh` queries and a shared stub is how a fixture stops being the
  thing under test. Nine cases: the clean control first, so a script that
  refused unconditionally cannot pass the rest; the exemption asserted rather
  than assumed; no gh, no auth, an unreachable tracker, an empty answer, a
  truncated one, a missing file, and a bare `#10` in prose.

  That last case went red, and it was right to. The comparison was written as
  `awk 'NR == FNR { named[...] }'`, which is the idiom for *still reading the
  first file* and is wrong the moment that file is empty: awk never runs the
  rule, `NR` and `FNR` then both count from 1 through the second file, and
  **every closed issue is filed as named**. A CHANGELOG with no links in it
  reported that it records everything, having looked at nothing. That is the
  empty-haystack shape, in the tool written to refuse it, and it would have sat
  there reporting agreement for as long as the file kept at least one link -
  which is forever, in practice, and is exactly why it needed a case that takes
  the last one away.

* **The 87 messages `show bpa` can print had no reader, and the suite deleted
  the evidence that would have given them one.**
  `appliance/selftest`, `hexarouting-install`, `bind-dataplane-nic` and
  `apply-tuning` each have a pair of readers that refuse a message no box
  printed and a line every box printed. `src/plugins/bpa/bpa_cli.c` is what an
  operator reads when a customer calls - `this answers for NOW ONLY. The block
  belonged to somebody else` is the sentence that stops traffic being
  attributed to the wrong subscriber - and nothing in this repository asserted
  about it.

  `tools/plugin-says.sh` is that reader for C, and the rule it uses is
  `test/say-coverage.sh`'s, not a second copy of it: the needle is still cut
  rather than globbed, still every literal fragment in order, still floored at
  `SAY_MIN`, and both halves and the report are the same code that reads the
  shell programs. What is new is one extraction, `say_messages_c`, because a
  value is spelt `%u` in C and `$nbad` in shell.

  **The boxes are a dataplane state, not a prompt**, which is why this reader
  is a tool and not a suite in `make test`: reaching "no subscriber holds a
  block right now" needs a pool with nobody on it, and `make test` runs with
  no root, no VPP and no network. So the corpus is the lab suite's own output,
  kept with `LAB_OUTPUT=<dir> sh tools/run-all-labs.sh`. It could not be
  before: `tools/run-all-labs.sh` deleted each lab's output with the run that
  made it, and the first attempt at this measurement read the SUMMARY logs
  instead and reported every message as unseen, which is an artefact of the
  corpus stated as a fact about the product. The default is unchanged and the
  `trap` stays - 87 labs times three passes is not something to accumulate on
  every release.

  `test/test_plugin_says.sh` drives the reader against a stub source and
  corpora it writes itself, so both halves and both floors have a green box
  and a red one: a corpus missing exactly one message is not something a real
  run hands you on request. It also pins the extraction against the real
  plugin - the count of call sites, the count with a needle to hunt for, and
  four of the sentences an operator reads during an incident, whole, because
  an extraction that truncated at the first source line break would still find
  87 sites and would hunt for half of every message.

* **The wiki quotes screens the box prints, and now something compares them.**
  `wiki/` is 32 published pages on the open internet and 61 of its fenced
  blocks quote a verdict the box says: `error:`, `warning:`, `[ ok ]`,
  `[warn]`, `[FAIL]`, `[info]`. Nothing read one of them. The sweep of
  2026-09-15 found ten stale by hand, one page at a time, and eight were
  stale the same way: a refusal that
  [#355](https://github.com/andrediashexa/hexarouting/issues/355) had split
  into rows of its own was still quoted as one long sentence, beside a
  paragraph telling the operator that the terminal would fold it and to look
  for the beginning of the sentence. True when it was written; by then it sent
  a reader looking for a fold that no longer happens.

  `test/test_wiki_screens.py` is the fourth reader of the public wiki, beside
  the three in `test/test_wiki_rules.py`. It builds a corpus out of the
  product's own sources - the VTY's f-strings, the appliance scripts'
  `ok`/`warn`/`bad`/`say_list`, the plugin's C format strings - drives twenty
  scenarios through `hexash.Shell` and its finding checks, and requires every
  quoted verdict to be a screen one of those two accounts for.

  **A matcher with wildcards is the classic check that cannot fail**, and most
  of these screens interpolate a value, so the defence against that is the
  design and it is argued in the file's docstring: driving beats matching and
  is counted separately on the report; a template must account for a whole row
  and more than half of it in literal text; a template that is nearly all hole
  never enters the corpus and the number refused is printed; a wildcard never
  crosses a row, because a paragraph is reconstructed only to hand it back to
  `console_prose` - or to `fold -s`, for the self-test - and the page's rows
  have to come back out of it character for character. That last one is what
  catches the eight. The loosest match the run leaned on is printed with its
  hole count, and so is the list of screens nothing could attribute: 22 of 61
  today, named on the screen with page and line, because the skip list is the
  honest edge of the rule and belongs where the next person reads it.

  Measured against the pages as they were before the sweep, it names five of
  the six stale screens that carried a verdict at all - the sixth had its
  sentence rewritten rather than re-folded, and the four remaining of the ten
  are tables and completion hints, which carry no verdict and are outside what
  this reads. It also refuses a screen with a word edited inside it, which is
  what a quoted refusal looks like after somebody repairs it by hand.

* **`tools/wiki-publish.sh`: the whole backlog in one command**
  ([#277](https://github.com/andrediashexa/hexarouting/issues/277)).
  `tools/wiki-push.sh` takes one file, and that is the right granularity: each
  push checks that one document and refuses if somebody edited it after the
  last pull. What it is not is a thing anybody does twenty-seven times by
  hand, and #277 is the measurement of that - the mirror stood 28 pages ahead
  of the live wiki from 2026-09-09, one of them telling an operator something
  about their box that had stopped being true, and the reason recorded on the
  issue is **"nobody did"**. A chore nobody does is a defect with a slower
  fuse.

  It **stops at the first refusal**, and that is the whole safety argument: a
  refusal means the live document changed after the mirror was pulled, so
  somebody is editing the wiki, and carrying on would publish the rest of the
  list over the top of whatever they are doing. It says how many went out
  before it stopped. `-n` says what would go and needs no token.

  The list comes from `tools/wiki-status.sh --updatable`, which is the same
  computation that prints the human report with a different rendering, so
  there is no second reader of git's status letters to keep true. It is the M
  list only: a page that exists here and not on the wiki has to be created by
  hand first, and feeding it to `wiki-push.sh` would stop the run on a refusal
  that is nobody editing anything.

* **The self-test says what tuning is IN EFFECT, not what was requested**
  ([#163](https://github.com/andrediashexa/hexarouting/issues/163)). Every
  tuning line this appliance printed read one side or the other: the generated
  GRUB file, or `/proc/cmdline`. Both are the **request**. A parameter can be
  lost at three separate steps - `update-grub` never ran, the box has not
  rebooted since, or the kernel saw it and refused it - and
  [#290](https://github.com/andrediashexa/hexarouting/issues/290) is the box
  where it was lost at the last one: a command line carrying `hugepagesz=1G
  hugepages=5` on a CPU with no 1 G page, for the life of that machine, while
  every reader went on reporting a tuned box. An appliance that says it is
  tuned when the kernel did not take the parameter is worse than one that says
  nothing.

  `hexarouting-tune --report-effective` reads each rule back off the running
  box and `appliance/selftest` renders it. **Sixteen rules** are covered - 11
  kernel command line parameters, 4 sysctls and the irqbalance CPU list - on
  top of the governor and the worker pinning, which already had readers. The
  readback table lives beside `render_grub()`, which is the component that
  writes those parameters, so the two lists cannot drift; `appliance/test_tune.py`
  takes the GRUB line the tuner really generates and requires every parameter
  on it to come back `in-effect` on a box that took it.

  **Four answers and not two.** "Requested and not in effect" does not collapse
  into "in effect", which would be the appliance claiming tuning it has not
  got, nor into "not requested", which would make a broken box look like a
  deliberate one; and a parameter this box exposes no way to read back is a
  fourth answer rather than a guess in either direction. Two rules land there
  on real hardware and are named: `rcu_nocbs` has no sysfs readback on 6.1
  outside root-only debugfs, and `irqbalance`'s CPU list is in the daemon's
  environ, which is root-only. One command line that missed the boot is
  reported once, not once per parameter on it, and the message says `Run
  update-grub and reboot` rather than leaving the operator a verdict and no
  move.

* **The other 70 VTY refusals, and the floor is zero.** The sweep below left
  70 of the VTY's 180 refusal sites unrendered and held them as a floor.
  Every one of them now has a box in `test/test_hexash_refusals.py`, so all
  180 have been watched printing and `UNRENDERED_AT_MOST` is **0** - the first
  time it has been. The next refusal added to `src/hexash/hexash.py` with no
  box added anywhere goes red on the next run with nothing to subtract from,
  which is the state that file was written to reach rather than a milestone.

  Arranged in the order the refusals matter rather than the order they are
  easy. 11 are the CGNAT globals and the IPFIX export, which are reached by
  `--load`, `rollback` and hexarouting.service without anybody at the prompt:
  a timeout this box has no field for, a session table named with one of its
  two numbers, and each way the export does not arrive - on a box where the
  allocation log **is** the connection record the Marco Civil asks for. 10 are
  a pool's data and the paths that run unattended: a commit that would re-map
  1200 subscribers onto different public addresses and stops to make the
  operator type `commit remap`, a `commit confirmed` window that is not a
  number, a confirm-revert that does not validate, the restart that gives a
  management card back, and a pool statement accepted in silence - which is
  #224's defect, where `show this` still showed the old value. 16 are the
  accounts and the box's own settings, where a refusal that does not fire ends
  with somebody holding a console cable: a plaintext password in
  `/etc/shadow`, which does not fail loudly but produces a password that never
  matches; `no user root`; an ssh value sshd would not take; a management
  record the parser cannot read. 13 are the interface statements, every one of
  which would otherwise be staged as an FRR statement and reach the TAP and
  not the packet path - the failure whose lab assertions were vacuous for that
  reason. The rest are a routing daemon that is not answering told apart from
  a protocol nobody configured, a policy no daemon is running to store, IS-IS
  frames that would never be punted to the kernel (#168), the interface
  sampler refusing to print a rate it cannot measure, the shell escape and the
  installer, and a pipe filter this box has not got.

  **69 of the 70 were shown red on their own mutation of the product**, each
  one a single line making the guard that reaches the refusal false, never the
  message edited - a reader keyed on the line is unmoved by the text - and
  each checked to take exactly one call site down with it. Sixteen of those
  guards protect the dereference on the line after them, so falsifying them
  ends in a traceback rather than a red box and a crash is not evidence; those
  sixteen were falsified one step further out instead, at the branch that
  matches the operator's line or the value the guard reads. The one that has
  no such mutation is named here rather than left to be discovered:
  `no candidate configuration` guards every use of the candidate in
  `do_commit()`, and every way of making its guard false ends in
  `AttributeError: 'NoneType' object has no attribute 'routing'`. Its box
  passes and its red was not shown.

* **The release gate compares the published release's title with the CHANGELOG
  heading** ([#340](https://github.com/andrediashexa/hexarouting/issues/340)).
  Nothing read the published releases at all. Measured over all 74: 66 titles
  are the heading and 8 are not, and four of those are **truncations** - 0.69.0,
  0.70.0, 0.71.0 and 0.46.0 each drop the second half, which is where the second
  defect that release fixed is named. The gate already argues this about the
  tag's note, that it is what an operator who does not clone the repository
  reads; the release page is the same audience with a wider reach, and the title
  is what somebody decides on when they are looking for the release that fixes
  what is biting them. It is placed beside the wiki and roadmap comparisons and
  **not behind them**: those have been red since 2026-09-09, so a check behind
  them would never have run once - the defect that pair already had, committed
  again while writing this and caught by running it. The eight already published
  are a repair by hand and the issue records them.

* **The VTY refuses 180 times, and 114 of those refusals had never been
  printed by anything.** `src/hexash/hexash.py` says no to the operator 180
  times, through `self.err()` and `self.err_dataplane()`. Measured by wrapping
  `err` and reading the VTY's own frames off the stack while every suite that
  drives it ran: 66 of those call sites had been printed by something, and 114
  had not. A refusal nobody has watched fire is one this repository cannot
  tell from a refusal that does not fire, and what it lets through is the
  configuration the refusal was written to stop - a pool the dataplane would
  reject, an MTU the driver never took, a uRPF the operator believes is
  dropping spoofed sources while the box translates them. Three refusals of
  exactly that shape were found and closed by hand in the last two days, each
  because somebody thought to look.

  `test/test_hexash_refusals.py` is what this project does instead of thinking
  to look. It is the second of the two readers at the end of
  `test/test_selftest.sh`, carried from shell to Python's AST: the call sites
  are parsed out of the product, the 18 suites that drive the VTY are derived
  from `test/suites.list` rather than listed - it is **19** on this tree, and
  that it moved without anybody editing this reader is the point of deriving
  it - and coverage is spent on the **line** that refused rather than on the
  message. That last part is the
  whole difference - nine call sites print "the dataplane does not have it"
  word for word, so a reader keyed on the text can neither tell them apart nor
  survive a mutation to it. There is no list to keep in step: a refusal added
  to the VTY with no box added anywhere goes red on the next run.

  46 boxes were arranged with it, in the order the refusals matter rather than
  the order they are easy: 23 for the interface verbs that talk to the
  dataplane (a name the dataplane has not got, told apart from a name it
  refused, on each of nine verbs; every one of those refusals coming back from
  the dataplane itself; a sub-interface whose parent is not on the box), 9 for
  a pool's data (half a mapping, the device's own check, a forward on a pool
  that is not being rebuilt, and each of the six pool writes a commit can fail
  at), 8 for the management path (ssh, snmp, the accounts, the tuner's file,
  a routing reload that failed, FRR silent at commit and at check, and a
  read-only account reaching for configuration mode) and 6 for the read-backs
  that catch a dataplane which accepted a message and applied nothing. Each
  was shown red on its own mutation of the product - the guard that reaches
  the refusal made false, never the message edited, because a reader keyed on
  the line is unmoved by the text and a test that takes its needle from the
  string it checks agrees with any change to that string.

  That takes the unrendered count from 114 to 70, and the 70 are **named by
  the reader and held as a floor that may only go down**, the idiom
  `test/test_off_run_coverage.sh` already uses for labs with no off run. No
  allowlist: an allowlist is read once and grown forever, and the failure this
  file exists for is a refusal nobody re-examines. What the reader cannot see
  is written in its header rather than left to be discovered - a suite that
  drove the VTY as a program rather than as a module, the labs, and the two
  call sites (`require()` and `say_dataplane_if_refusal()`) that print many
  different refusals from one line.

* **Eleven of the release gate's fourteen reachable refusals are driven now,
  up from six.** Fourteen was the reachable count that day; the gate has grown
  two refusals since, and `sh test/test_release_gate.sh` now prints *"16 of the
  gate's 24 refusals are reachable without an image, and this file drives 11 of
  them"*. The five added protect what an operator reads about their own
  version: a commit that is not a tag; a release tag whose `## Unreleased`
  section still carries entries, so the release contains work it does not
  claim; a shipped release's section edited after the fact, which changes what
  a box somebody is running is said to do; the guard for a section that
  extracts to nothing, without which the duplicate-heading check passes over
  anything; and a CHANGELOG carrying one entry twice, where an operator cannot
  tell which release carried the fix. Everything after
  `RELEASE_GATE_CHECK_ONLY` still needs a 321 MB image built, booted and
  installed, and the suite says so as a number rather than leaving it assumed.

* **Every lab is now accounted for on whether its checks have ever been seen
  to fail, or it is named.** `tools/run-all-labs.sh` runs some labs a third
  time with the feature they test switched off and requires each to go red
  against a floor the lab declares - and that is the only mechanism in this
  repository that renders a lab assertion's **red** branch. Measured while
  sweeping them: 425 of the 1381 assertions under `lab/` have their failure
  shown on every release run, and 956 do not. There were three ways a lab could
  be accounted for - an off mode plus a floor, a `NOT_LABS` entry saying why it
  is not run at all, or nothing - and 32 labs were in the third, with no file
  anywhere recording whether that was a decision. `test/test_off_run_coverage.sh`
  makes that a fourth, explicit way (`NO-OFF-RUN:` with a reason, which has to
  be a sentence rather than a stamp) and holds the unaccounted count as a
  **floor that may only go down**, in the same idiom as `OFF-RED-AT-LEAST`.
  Writing 32 reasons tonight would have meant inventing most of them, and a
  rubber stamp is worse than the silence it replaces. It also refuses the two
  halves coming apart: an off mode with no floor accepts whatever that run
  returns, and a floor with no off mode is a number nobody reads. `lab/upgrade.sh`
  already carried its reason in prose and now carries it in the form the check
  can find.


* **Six of the release gate's refusals now have assertions, and it had two.**
  `tools/release-gate.sh` carried 22 refusals when this was written and
  carries **24** now, which is what
  `grep -c 'echo "error:' tools/release-gate.sh` answers. Its own header calls
  it "the only thing in this project that has ever caught a release mistake".
  Until tonight the only two anything had watched fire were the two added
  tonight;
  the rest had been seen only when a real release went wrong. A refusal nobody
  has watched fire is a refusal that may not fire, which is #335's finding in
  the place it would cost the most. `test/test_release_gate.sh` clones the
  newest tag, **breaks exactly one thing**, re-tags and requires that one
  refusal - starting from a tree that passes is what makes each case about its
  own break rather than about the scaffolding. It also says, as a number, how
  many of them it reaches: 14 of the 22 that day, **16 of the 24** on this
  tree, because the suite counts both off the gate itself rather than quoting
  a figure. Everything after `RELEASE_GATE_CHECK_ONLY` needs a 321 MB image
  built, booted and installed, and is still measured only by running a real
  release.


* **The release gate refuses a release that would ship past a stale wiki**
  ([#277](https://github.com/andrediashexa/hexarouting/issues/277)). Twenty
  seven pages have been corrected in this repository and are still showing
  operators the text from the pull on 2026-09-09, and a twenty eighth was
  written here and does not exist on the wiki at all. One of those corrections
  is [#254](https://github.com/andrediashexa/hexarouting/issues/254): the
  published page tells an operator that a refused `commit` applies nothing,
  which stopped being true when the FRR read-back check became a refusal that
  leaves the box half-configured, and it is read by somebody looking at that
  refusal on their screen deciding what state their box is in. A correction
  sitting in a mirror nobody can read is not a correction yet.
  `tools/wiki-status.sh` has been able to say this for a while and nothing
  asked it. The gate asks now, before it builds anything, and repeats what
  that script said rather than listing pages of its own. It is the gate and
  not `make test` on purpose: a check that goes red on every run is one people
  learn to run around, and the person who can publish a page is the one
  holding the token at the moment a release is cut.


* **A lab performs the bind, on a real guest, for the first time**
  ([#329](https://github.com/andrediashexa/hexarouting/issues/329)). Handing a
  card to the dataplane means unbinding it from its kernel driver and binding
  it to `vfio-pci` or `uio_pci_generic`. Nothing in this repository had ever
  performed that bind. `lab/one-nic.sh` models it and says so in its own
  header, because a container has no PCI device to hand DPDK; `lab/install-iso.sh`
  booted a real qemu guest with a real virtio card and never asked the box to
  take it. What was proven was the decision and the boot-time replay - the
  driver change itself was unmeasured, on the path most deployments will
  actually run: a virtual machine with one virtio card and no IOMMU. Two new
  phases ask the box, through the VTY, with the one statement an operator has
  on a single-card machine, and then assert the four things that have to be
  true together afterwards: the card is on a driver DPDK can use and not the
  kernel driver it started on, Linux no longer has the netdev, the management
  address moved off the card, and the dataplane holds an interface that is not
  `local0`. The network reading is taken before this phase logs in at all,
  because a box that takes its card and stops answering has eaten its own
  management port, and on a single-card machine that means somebody walks to
  it.

  It went red on its first run, against the published 0.74.2 medium, and what
  it found is [#332](https://github.com/andrediashexa/hexarouting/issues/332):
  the box binds the card, Linux releases it, the dataplane holds it, and the
  box comes back with no address on any interface and nobody can reach it. The
  boot self-test on that same console says so - no interface is paired into
  the kernel - so the diagnosis was already right and only the doing was
  missing. That is what an unmeasured claim costs: the sentence was on the
  public page a virtual-machine operator reads, and the release gate now
  refuses a release until it is true.

## 0.74.2 - what the documents said about installing, and a check that could not fail

[#226](https://github.com/andrediashexa/hexarouting/issues/226), [#321](https://github.com/andrediashexa/hexarouting/issues/321), [#322](https://github.com/andrediashexa/hexarouting/issues/322), [#323](https://github.com/andrediashexa/hexarouting/issues/323), [#324](https://github.com/andrediashexa/hexarouting/issues/324), [#325](https://github.com/andrediashexa/hexarouting/issues/325), [#326](https://github.com/andrediashexa/hexarouting/issues/326).


### Fixed

* **The English rule binds every `*.md` and the check read 11 of 54**
  ([#323](https://github.com/andrediashexa/hexarouting/issues/323)).
  `CONTRIBUTING.md` rule 1 says documentation MUST be in English and binds it
  to "every `*.md` added to this repository". `test/test_english_only.py` walks
  directories, so `CONTRIBUTING.md`, `README.md` and `TESTING.md` - the three
  documents most likely to be read first - sat outside every one of them.

  And the rule was **broken thirty-five times on purpose** with the exception
  written down only inside the check: `wiki/` is the operator wiki's mirror,
  and `PADRAO-DOCUMENTACAO.md` and `PROMPT-DOCUMENTAR-WIKI.md` are the standard
  and the brief for writing it. A MUST that is broken thirty-five times and
  says so nowhere teaches a reader that the MUST is decorative. The exception
  is in `CONTRIBUTING.md` now, where the rule is.

  `CHANGELOG.md` is excused for a different reason and it is worth stating:
  it **quotes** Portuguese strings the product used to carry, inside sections
  that have been tagged - and this file's own rule is that a tagged section
  describes a medium somebody is running and must not be edited. Those
  quotations cannot be corrected even in principle.

  Two walks became one on the way. The guard that asks whether the check is
  reading anything did its own walk over the directories alone, so with the
  root markdown deleted from the real walk it still reported six root files
  and passed. It counts what the walk returned now.



* **The unattended-install tables left out the four answers that decide whether
  the box can be reached**
  ([#322](https://github.com/andrediashexa/hexarouting/issues/322)).
  `docs/appliance.md` has two reference tables, one for the kernel command line
  and one for the answer file, and both listed **seven** keys where
  `appliance/hexarouting-install` accepts **eleven**. The four missing ones were
  exactly `mgmt-interface`, `mgmt-address`, `mgmt-gateway` and `mgmt-dns`.

  An unattended install written from those tables answers nothing about the
  network and takes the DHCP default - and the same page warns, 250 lines
  above, what that costs on a box where nothing replies. That is
  [#303](https://github.com/andrediashexa/hexarouting/issues/303)'s box,
  reached by reading the documentation rather than by the installer being
  wrong.

  **One holder, and it is a message an operator reads:** the installer prints
  every key it knows when it refuses one, because it has to. The check reads
  that refusal and looks for each key **inside the two tables** rather than
  anywhere on the page. The first version searched the whole document and
  passed with a key deleted from both tables, because the page discusses the
  management network in prose elsewhere - and what an unattended install is
  written from is the table.

* **A count above a table, and a transcript the box stopped printing**
  ([#321](https://github.com/andrediashexa/hexarouting/issues/321),
  [#326](https://github.com/andrediashexa/hexarouting/issues/326)).
  `docs/appliance.md` said **"Two refusals fire at the prompt"** over a table of
  three. #303 added the third row and left the count. The sentence states no
  number now: the table is the list, and a number in the sentence above a table
  is a number that outlives the table it describes. The third row also named
  one of the three conditions that fire it, where the code refuses on a missing
  address **or** gateway **or** MAC.

  And the page quoted the tuner as printing `1G hugepages: 6 requested`. The
  box prints `6 GB requested`, and on a machine without 1 GB pages the line
  begins `2M hugepages:`. The commit that rewrote that line landed three hours
  after the one that quoted it correctly, and did not touch the page.

  **Nothing could have compared them**, which is why it stood: the report was
  printed inline in `main()`, so there was no way to render it without running
  the whole tuner. It is `render_report()` now, beside the three `render_*()`
  functions it sat among, and `appliance/test_tune.py` drives the tuner against
  a synthetic root and reads the line back out of its real output. **The fixed
  text, not the numbers**: the page's box is not the fixture's box and the
  figures should differ, but the wording between them is what a reader compares
  their own console against, and that is what changed underneath the page.

* **Two invariants in `docs/context.md` that stopped being true, and a number
  that was never measured**
  ([#324](https://github.com/andrediashexa/hexarouting/issues/324),
  [#325](https://github.com/andrediashexa/hexarouting/issues/325)).

  **1.8 said "every merge into `master` is a release."** `CONTRIBUTING.md`,
  which that section cites as authoritative, excepts a whole class in as many
  words: a fix merged under the standing exception rides in the next tagged
  release, because a version number that moves for a corrected typo tells an
  operator nothing about whether they should upgrade. Measured: **300
  first-parent merges, 78 tags**, and the last released window is six merges to
  one release. The invariant is the other direction and it holds - a release
  has a version bump, an annotated tag, a CHANGELOG entry and a published
  Release, all four or none. An invariant stated one step too wide is one a
  reader checks once, finds false, and stops checking.

  **1.9 said `wiki/` is a read-only mirror and editing it changes nothing.**
  Since [#277](https://github.com/andrediashexa/hexarouting/issues/277) editing
  it is exactly how a page is changed: the correction is written there,
  `wiki-status.sh` lists what is unpublished, and `wiki-push.sh <file>`
  publishes one page. What is still true is that the edit reaches nobody until
  it is pushed, which is what that issue was about.

  **And the throughput section justified publishing no figure with a measured
  one.** "The bench resolves differences above roughly 3%" appears nowhere
  else: not in `lab/throughput.sh`, not in
  [#28](https://github.com/andrediashexa/hexarouting/issues/28), not in this
  file. The lab runs once, keeps no repeats and reports no spread, so how small
  a change it could tell from run-to-run variation has never been measured -
  which is a **better** argument for claiming nothing, and is what the section
  says now. Publishing no number was right; supporting it with an unmeasured
  one was the same mistake in miniature.
### Internal

* **The one check that guarded paired address pooling could not fail**
  ([#226](https://github.com/andrediashexa/hexarouting/issues/226)).
  `lab/detreserve.sh` asserts that a deterministic pool's reserve block lands
  on the subscriber's own public address, which is the RFC 4787 REQ-2 property
  the box states in four places and the only place in the product where
  "prefer the subscriber's own address" is actually implemented. It ran on a
  pool with ONE public address, where there is nowhere else for the block to
  land, so the assertion was true whatever the allocator did.

  Measured, not argued: with the preference loop in
  `bpa_dyn_acquire_extra()` switched off, that check stayed green and only the
  new one went red.

  The lab now runs a second pool with four public addresses, where the
  subscribers extend in descending index order while the free list hands
  blocks out in ascending order, so the block on top of the stack is never the
  one the subscriber's own address holds and the box has to choose. A guard
  beside it asserts the four holders really are on four different addresses,
  so the day the pool stops offering anywhere else the measurement says so
  instead of passing. A third pool reports, without asserting, what the same
  preference does one block further along the address, which is where the
  worker sharding starts to answer differently: 4 of 4 on the holder's own
  address at 0 and at 2 workers, 0 of 4 at 3, with one subscriber getting no
  extension at all. That number is the cost of the preference under sharding
  and it is read from #226.

  The off run goes red on ten checks where it went red on nine, and the header
  floor says so.

## 0.74.1 - fifteen things this product said that were not true, and the checks that hold them

[#163](https://github.com/andrediashexa/hexarouting/issues/163), [#172](https://github.com/andrediashexa/hexarouting/issues/172), [#248](https://github.com/andrediashexa/hexarouting/issues/248), [#277](https://github.com/andrediashexa/hexarouting/issues/277), [#288](https://github.com/andrediashexa/hexarouting/issues/288), [#304](https://github.com/andrediashexa/hexarouting/issues/304), [#306](https://github.com/andrediashexa/hexarouting/issues/306), [#307](https://github.com/andrediashexa/hexarouting/issues/307), [#308](https://github.com/andrediashexa/hexarouting/issues/308), [#309](https://github.com/andrediashexa/hexarouting/issues/309), [#310](https://github.com/andrediashexa/hexarouting/issues/310), [#311](https://github.com/andrediashexa/hexarouting/issues/311), [#312](https://github.com/andrediashexa/hexarouting/issues/312), [#313](https://github.com/andrediashexa/hexarouting/issues/313), [#314](https://github.com/andrediashexa/hexarouting/issues/314), [#315](https://github.com/andrediashexa/hexarouting/issues/315), [#316](https://github.com/andrediashexa/hexarouting/issues/316), [#317](https://github.com/andrediashexa/hexarouting/issues/317), [#318](https://github.com/andrediashexa/hexarouting/issues/318), [#319](https://github.com/andrediashexa/hexarouting/issues/319), [#320](https://github.com/andrediashexa/hexarouting/issues/320).

Who said them, because "the box" would be wrong for eleven of the fifteen:
four were printed by the box
([#308](https://github.com/andrediashexa/hexarouting/issues/308),
[#309](https://github.com/andrediashexa/hexarouting/issues/309),
[#310](https://github.com/andrediashexa/hexarouting/issues/310),
[#312](https://github.com/andrediashexa/hexarouting/issues/312)), two by the
operator wiki, three by `TESTING.md`, five by `docs/`, and one was the release
gate doing something other than what it says it does
([#311](https://github.com/andrediashexa/hexarouting/issues/311)).



### Changed

* **The release checklist now says to publish the medium's `sha256`.** Every
  release so far has carried one and nothing said to. `tools/release-gate.sh`
  prints it in the same breath as "ready to publish", so it has always been to
  hand and has always been pasted in by whoever was cutting the release, which
  is the definition of a step that goes missing the first time somebody else
  does it. It is the only thing an operator who downloaded a 300 MB file over a
  link they do not control can check it with, before installing it on a box
  that will carry subscribers.

* **The refusal to build a custom kernel now names the mechanism it rests on**
  ([#163](https://github.com/andrediashexa/hexarouting/issues/163)).
  `docs/appliance.md` refuses a custom-built kernel chiefly because the
  appliance would stop inheriting Debian's kernel security updates, and nothing
  in the tree said how it inherits them in the first place. It is one word in
  `appliance/build-iso.sh`: the debootstrap `--include` list names
  `linux-image-amd64`, a metapackage carrying no version and no pin, so the box
  follows Debian's stable kernel without anybody deciding to. Read off two
  boxes a month apart rather than asserted: the medium the boot lab actually
  booted says `Linux version 6.1.0-50-amd64 ... Debian 6.1.176-1 (2026-07-02)`
  in its console log, while this tree develops against `6.1.0-52-amd64`,
  `Debian 6.1.180-1 (2026-08-03)`. Two ABI revisions, and no commit in this
  repository selected either one.

  `test/test_kernel_tuning.py` grew three assertions, because that argument was
  resting on a line anybody could change for an unrelated reason - freezing a
  kernel to reproduce a bug arrives looking like a debugging convenience rather
  than like the decision the section refuses. Each was shown failing on its
  own: dropping the package from `--include`, pinning it while keeping its name
  so that only the pin check fires, and a document that stops naming it.

  ```
  FAIL: appliance/build-iso.sh pins a kernel version:
  ['linux-image-amd64=6.1.180-1']. An unpinned metapackage is what makes the
  security update arrive without a decision, so a pin takes on the maintenance
  cost docs/appliance.md refuses a custom kernel over, without the argument
  that section demands
  ```

  **No kernel parameter was added and nothing a box boots with changed.** The
  eight tuning candidates in that section stay hypotheses deferred to
  [#28](https://github.com/andrediashexa/hexarouting/issues/28), which is the
  half of #163 that is still blocked.

### Fixed

* **The three documents nobody had read end to end were read end to end, and
  five statements in them do not hold.** An earlier sweep closed ten defects in
  `docs/` and recorded one gap in its own coverage: `docs/architecture.md`
  (2,063 lines), `docs/routing.md` (4,194) and `docs/logging.md` (869) had
  never been read whole. Nothing here is fixed and nothing in the product
  changed; the five are filed, and the reading is recorded in the new
  `docs/docs-sweep.md` so the next reader can tell a claim that was checked
  from one that was read past.

  [#318](https://github.com/andrediashexa/hexarouting/issues/318): `routing.md`
  tells a reader the undefined-route-map refusal is what the box prints "as one
  line of 221 columns" and sends them to the width section for why it "was left
  alone". [#276](https://github.com/andrediashexa/hexarouting/issues/276)
  replaced that shape with `Shell.say_finding()`, which prints four rows with
  the widest at 77, and rewrote the section the sentence points at without
  touching the sentence. Driven in-process, 221 is the length of the shape
  nothing builds any more.

  [#319](https://github.com/andrediashexa/hexarouting/issues/319): two counts
  `routing.md` states as measured are wrong, and each is repeated in a comment
  in the test that measures the same thing, beside an assertion that says
  otherwise. The provider-edge read-back is three statements of twenty-four
  marked, which `routing.md:1347` also says and `routing.md:3946` contradicts
  three sections later; and the long IPv6 statement is 67 columns, not 66,
  which is 87 with the indent and the mark rather than 86.

  [#320](https://github.com/andrediashexa/hexarouting/issues/320):
  `logging.md`'s TL;DR sizes per-flow NAT at ~2.4 GB/day and its own Volume
  table sizes the same 40 million records at ~1.6 GB, from assumptions the same
  page states. The ratio is the commercial argument for BPA and the page gives
  it as 30x in one place and 20x in the other. "All 38 suites" is 54, and was
  39 at the commit that wrote one of the two occurrences, which is
  [#317](https://github.com/andrediashexa/hexarouting/issues/317)'s first
  number in a second file.

  Four of the five are numbers, which is why they are the ones a sweep of prose
  can be held to. What held is the larger half and is listed by class rather
  than asserted: every C symbol, VPP node name and struct field; all twenty-nine
  `VLIB_CLI_COMMAND` paths against what the pages name; the four RFC timeout
  floors against the X-macro; all five ALG off-run counts against their labs'
  `OFF-RED-AT-LEAST` headers; the seven IPFIX elements against
  `bpa_block_elements[]`; and `ref/check_magic.py` run, which prints
  `architecture.md`'s sentence word for word. `test/test_doc_commands.py`
  already covers the wiki's commands and every repo-relative path in every
  markdown file, so none of this is in scope there, and a number in prose is in
  neither class.

* **The VTY refused its own `prohibit-password`, because sshd answers with the
  other spelling**
  ([#304](https://github.com/andrediashexa/hexarouting/issues/304)). OpenSSH
  takes `prohibit-password` and `without-password` as two names for one
  `PermitRootLogin` setting, and `sshd -T` - which is how the VTY reads the
  effective daemon configuration - canonicalises to the OLD one. Measured on
  OpenSSH 9.2: a config saying `prohibit-password` reads back as
  `permitrootlogin without-password`, and `validate_ssh` answers

  ```
  ssh permit-root-login 'without-password': expected one of yes, no,
  prohibit-password, forced-commands-only
  ```

  The VTY builds its candidate from the running configuration, so this is not
  only a refused `ssh` statement: a box in that state cannot commit a change to
  anything else, and it fails to load its own saved configuration at boot -
  which is a box that comes up unconfigured on a machine nobody is watching.

  Not reachable as the product ships, because `appliance/install.sh` writes
  `PermitRootLogin yes`. Reachable on any box whose operator tightened it,
  which is the box most likely to have been tightened for a reason. It was
  found while building the one-NIC lab for
  [#303](https://github.com/andrediashexa/hexarouting/issues/303), where a
  fixture worked around it - and a workaround in a fixture is a defect somebody
  meets without one.

  The daemon's other name is translated **on the way in**, in one place, rather
  than added to the list of values the VTY accepts. Two names for one setting
  inside the product would reach `show running-config` and the saved file, and
  an operator comparing two boxes would see a difference that is not one. The
  new `test/test_ssh_settings.py` holds both halves: anything `read_ssh()`
  returns must pass `validate_ssh()`, and the completion tree an operator sees
  with `?` must offer exactly the values the box accepts - so the tempting fix
  of widening the list alone goes red.

* **The bind message lost the name of the card it was taking, on every VM.**
  `bind-dataplane-nic` resolves a netdev to its PCI address twice: once for the
  refusal that stops the management card being unbound, and once, thirty lines
  later, for the line it prints about what it is doing. 0.74.0 fixed the first
  and left the second comparing the basename of the `device` link against a PCI
  address - which on a virtio guest is `virtio7` and never matches. So the
  refusal works now and the message still reads

  ```
  bind: 0000:04:00.0 from virtio-pci to vfio-pci
  ```

  with the one word in it an operator recognises missing.

  Harmless on its own and worth the entry for its shape: two readers of one
  fact with only one of them corrected is what had the release gate refuse
  0.72.0 twice, once after the first reader had already been fixed. Both
  readers go through the same `pci_addr_of()` now, and `test/test_bind_nic.sh`
  binds a virtio device and reads the line back.

* **An install from a medium that carries ssh host keys no longer puts them on
  the disk** ([#248](https://github.com/andrediashexa/hexarouting/issues/248)).
  `ssh-keygen -A` generates only the key types that are MISSING, so on a target
  that already has a set it is a no-op that exits 0 and prints nothing.
  Measured on the published `hexarouting-0.37.0-ab23bd7-dirty.iso`, whose
  squashfs carries `ssh_host_{rsa,ecdsa,ed25519}_key` stamped 2026-09-08 16:43
  against a squashfs made at 16:46:15: unpacking it with the installer's own
  `unsquashfs -f -d` line and then running `ssh-keygen -A` over the result left
  all three fingerprints exactly as the medium had them, ed25519
  `SHA256:hwhc4Nfxh3lzwZNoIlON9ehRISKN1BBeo1wcEQi/DJg`, comment
  `root@a3bcac8dc76e`, which is the build container. The installer said
  "generating this box's own ssh host keys" and generated none.

  It reached no box, because `build-iso.sh` empties the image first and the
  0.74.0 ISO built by the release gate carries zero host keys, measured with
  `unsquashfs -ll` on the artefact. But that made the whole guarantee a
  property of the medium: the install step had no opinion of its own and could
  not fail. `hexarouting-install` now removes whatever `/etc/ssh/ssh_host_*`
  the medium handed over before it generates, and says on the console when a
  medium carried any, because an operator holding such a medium is holding the
  medium every other box was installed from. Driven against the real 0.37.0
  medium, the disk that comes out answers with a key made on it rather than
  with `root@a3bcac8dc76e`.

  The public halves go with the private ones. `ssh-keygen -A` rewrites the
  `.pub` of every key it generates, so the ones it can leave behind are the
  types it does not generate: measured here, `ssh-keygen -A` on OpenSSH_9.2p1
  Debian-2+deb12u10 writes RSA, ECDSA and ED25519 and nothing else.

  Boxes already racked are not reached by any of this, and were never going to
  be: the installer on a medium that carries keys is the one that shipped on
  that medium. The boot self-test is what reaches them, on the console they are
  standing at, and it already ships. Driving `appliance/selftest` against a
  root unpacked from `hexarouting-0.37.0-ab23bd7-dirty.iso` reports "the ssh
  host keys were not generated on this box", names
  `ssh_host_ecdsa_key ssh_host_ed25519_key ssh_host_rsa_key`, and gives the
  three commands that regenerate them. The disclosure itself, and where it is
  published, remain the maintainer's to decide.

  And the install reads the EFFECT rather than the exit status.
  `ssh-keygen -A` exits 0 having written nothing whenever it believes the keys
  are there, which is not a hypothetical: it is the whole of how this defect
  worked. An install that takes the exit status for the answer reports a
  finished box over an `/etc/ssh` with no host key in it, and `sshd` is the
  only way back to a racked one. The function now looks for a key afterwards,
  and the test drives a `ssh-keygen` that succeeds and writes nothing.

* **The mirror under `wiki/` stood 28 pages ahead of the wiki operators read,
  and the documented way to publish them did not work.** Measured with
  `git diff --name-status` between the last commit that wrote `wiki/.pulled`
  (d53b44e, 2026-09-09) and v0.74.0: 27 pages corrected here and still showing
  the old text, plus one page written here that the wiki does not have at all.
  One of the 27 is the page that told an operator a refused `commit` applies
  nothing, which stopped being true when the FRR read-back check became a
  refusal that leaves the box half-configured.

  Three things stopped it being publishable. `tools/wiki-push.sh` took a
  document id that lived nowhere in the repository, so publishing 28 pages
  meant finding 28 ids in a web interface first. `tools/wiki-pull.sh` deletes
  `wiki/` and rewrites it from the live wiki, so the published order - pull
  first, then push - would have destroyed all 28 before one went out. And the
  pull command written in `CONTRIBUTING.md`, `docs/wiki-audit.md` and
  `wiki/README.md` set `WIKI_HOST`, which is read by nothing in this tree:
  `grep -rn WIKI_HOST tools/ src/ appliance/` returns no line, and the script
  exits on the missing `WIKI_TOKEN` every time.

  Now: the pull writes `wiki/.ids`, the document id beside the path the same
  line decides, so `sh tools/wiki-push.sh <file>` publishes a page on its own.
  A file with no entry is refused rather than aimed at a guessed document -
  `documents.update` cannot create one. The pull refuses while unpublished
  drafts are outstanding, naming them, and `WIKI_PULL_DISCARD=1` is how you say
  you meant it. The three documents say push first, then pull.

- **Nothing could answer "what is drafted here and not published yet".** Added
  `tools/wiki-status.sh`. The holder is the history and not a second file that
  could disagree with it: the last commit touching `wiki/.pulled` is the last
  moment the mirror and the wiki were the same text, so every page changed
  after it is unpublished. No page content is parsed and no message is matched.
  Added and modified pages are reported apart, because a page the wiki has
  never seen needs creating there first and cannot be pushed at all.

  The 28 pages themselves are still unpublished. Publishing needs `WIKI_TOKEN`,
  which no session in this repository has; the wiki answers
  `{"ok":false,"error":"authentication_required"}` to an unauthenticated read.

  Those two fixes then deadlocked each other, which is the state somebody
  arriving with the token is actually in: the push needs `wiki/.ids`, only the
  pull writes it, and the pull refuses while the drafts are outstanding. The
  only ways out were the 28 ids by hand that the index removed, or throwing the
  work away. `WIKI_IDS_ONLY=1 sh tools/wiki-pull.sh` fetches the index alone -
  same walk and the same line deciding a page's path and its id together, so it
  cannot describe a layout the pull would not produce - and writes nothing
  else: no page, no `.pulled`, no README stamp, because none of those is true
  of a mirror it did not refresh. The refusal names it, since a refusal with no
  way past it is a script somebody edits out.

* **The roadmap stated a measurement this project had already published a
  correction for.** [#288](https://github.com/andrediashexa/hexarouting/issues/288)
  quoted a cost for `statseg { size 1G }` that `docs/appliance.md` corrected in
  the same release that shipped the self-test check: in the table that figure
  came from it is the RSS difference between 0 and 40,000 **sessions** on a box
  that already had the 1 GB segment, so it is the cost of the traffic and not of
  the segment. `docs/roadmap.md` restated the retracted
  number as fact anyway, in a row written from the issue body, and also still
  described the box as dying with "nothing said about what ran out" after the
  boot self-test had started saying it.

  The row is replaced by the decision it was waiting on, which is that
  **`hexarouting-tune` does not size the statistics segment and should not**.
  Measured against this product's own RAM floor, 4 GB and the shipped
  `HEXA_SUBSCRIBERS=200000`, the tuner already reports `5 GB of hugepages were
  needed ... Capped at 2 GB`; sizing the segment from that subscriber count
  would ask for a further 553 MB at the measured 2.9 kB per interface, out of
  the 2 GB left outside hugepages, for interfaces that never appear. The number
  stays with the operator in `/etc/hexarouting/vpp-base.conf`, which the tuner
  concatenates ahead of its own stanzas and therefore never overrides.

  `test/test_doc_copies.py` gains the reader that was missing: `docs/appliance.md`
  is the one holder of that figure, because naming it is how the correction is
  made, and no other document may restate it. Shown failing on
  both halves, a second document naming the figure and the holder losing the
  correction. No product behaviour changed.
[#306](https://github.com/andrediashexa/hexarouting/issues/306).

* **`show interface` counts what can forward, and says so**
  ([#306](https://github.com/andrediashexa/hexarouting/issues/306)). Read off
  the console of the 0.74.0 installation medium during that tag's release
  gate: the box printed `the dataplane has no interface to forward on` and,
  four lines below it, `1 interface(s) on the dataplane`. The one it counted
  was `local0`, VPP's own null interface, which forwards nothing. Two
  statements about the same box, in one screen, that read as a contradiction.

  The issue left one case unmeasured and it is the one with a cost. Measured
  here on a box with two NICs, against the same fixtures the suite uses:
  `show interface` reported `3 interface(s) on the dataplane`, and
  `show interface description` reported `1 of 3 interface(s) described`. An
  operator who installed two cards read three, and a description column that
  can never be filled - `local0` takes no description - reported progress
  towards a total it could never reach. The empty box was the visible half of
  a count that was off by one on every box.

  The count is now of interfaces that can forward, and the wording says so:
  `2 interface(s) on the dataplane can forward`. Naming what was in the old
  count instead - "1 interface(s), including local0" - was the other candidate
  and was rejected: it leaves the operator doing arithmetic underneath a
  sentence that had already answered the question, it does not repair the
  description denominator, and it keeps a number on the screen that disagrees
  with the diagnosis above it.

  **`local0` is still listed.** Hiding an interface the dataplane has is how a
  listing stops being the thing people reach for when something is off. What
  the choice costs is that the count no longer matches the number of rows, and
  that is paid by a line under the count rather than by dropping the row:
  `local0 is listed above but not counted: it is the dataplane's own null
  interface and forwards nothing`.

  Three views printed that count - `show interface`, `show interface terse`
  and `show interface description` - each computing it for itself, which is
  the shape that had the release gate refuse 0.72.0 twice. They now read one
  holder, `Shell.forwarding_count()`, which returns the number and the line
  that has to go under it. Breaking any one of the three turns that view's
  assertions red; breaking the holder turns all twelve red at once.

* **The release gate installed a different medium from the one it booted, and
  published the one it never booted**
  ([#307](https://github.com/andrediashexa/hexarouting/issues/307)).
  `make test-install` depended on the phony `iso` target, so the gate built a
  medium, booted it, and then built a **second** one over the same path and
  installed that. Measured on the v0.74.0 run: the boot phase finished at
  18:02:23 and the file's mtime is 18:07:51.

  The whole argument for the install phase is that booting a medium and
  installing it are different claims about **the same medium**. They were
  claims about two builds, and the `sha256` the gate prints for publication is
  taken at the end, so it described the build that was installed and never
  booted. The boot phase went through `tools/lab.sh`, which does not rebuild,
  and the install phase went through `make`, which does: the asymmetry is what
  hid it.

  `tools/iso-ready.sh` answers whether the medium this tree would build is
  already here and is newer than everything that goes into it. Absent, the
  caller builds one; present and current, it is used, which is the fix;
  present and **stale**, the caller builds one, because quietly installing
  yesterday's image trades one wrong medium for another and that one has no
  boot behind it at all.

  The gate also records the medium's `sha256` before the boot and compares it
  after the install, so anything that replaces the file between the two is
  caught rather than only this cause. A check that knows one cause stops
  working the day there is a second.

  Costs five and a half minutes less per release as a side effect, on the one
  tool this project says must be what cuts a release.

  **`test-mirror` kept its prerequisite through that fix**, which is the same
  defect one target over and the shape that had the release gate refuse 0.72.0
  twice: two readers of one fact, only one corrected. Found by reading the
  Makefile for others rather than by anything going red, because the check
  written with the fix named `test-install` and `test-iso` by hand.

  It does not name them any more. The targets it checks are **derived**: the
  scripts that open a medium are the ones naming it through
  `tools/version-stamp.sh --filename`, which is the single place that
  calculation lives, and the targets are the Makefile recipes that invoke those
  scripts or the wrapper around one. Measured on the way: "calls `ISO_RUN`" is
  not the same set, because `hw-down` does that and opens no image, and an
  unanchored script name matched `lab/hw-lab.sh` as a substring of
  `tools/lab.sh`.

* **The comment over `bpa-expire-process` claimed a bound the barrier does not
  have** ([#172](https://github.com/andrediashexa/hexarouting/issues/172)). It
  said "the budget keeps each pass short: at 10k sessions the barrier is held
  for microseconds", which is true of the session walk and of nothing else in
  that acquisition. Read from the source: the per-pool `bpa_dyn_reap()` loop,
  `bpa_blocks_log_all()` and `bpa_log_drain()` all run inside the same barrier,
  and `BPA_EXPIRE_BUDGET` bounds none of them. On a dynamic-mode box with
  logging enabled the held interval therefore follows the logging load and the
  pool count, not the session table.

  No behaviour changes. The open half of that issue is the held-barrier
  interval measured under control-plane load, which needs hardware a virtio
  guest cannot stand in for, and the comment was the thing that would have sent
  whoever takes it to vary the wrong knob. `docs/appliance.md` points at the
  comment rather than restating the list, so there is one copy to keep true.

* **A release note could name a file the tree does not have.** Nothing compared
  the two, and the paths in a changelog entry are what a reader follows: the
  entry for [#307](https://github.com/andrediashexa/hexarouting/issues/307)
  names `tools/iso-ready.sh`, the one for
  [#304](https://github.com/andrediashexa/hexarouting/issues/304) names
  `test/test_ssh_settings.py`, and a rename between the branch and the merge
  would leave either of them pointing at nothing.

  `test/test_changelog_landing.py` checks the paths in the **unreleased**
  section only, and that is the whole design. A tagged section describes a
  medium somebody is running and must not be edited, so a file renamed after it
  shipped leaves that section pointing at a name the tree no longer has and
  there is nothing to be done about it. The section being written is the one
  somebody can still fix.

  Sound for the reason `test/test_doc_copies.py`'s header gives for rejecting
  its opposite: the holder is the filesystem. A path either resolves or it does
  not, and it cannot be reworded into passing. Absolute paths are skipped,
  because `/etc/hexarouting/vpp-base.conf` is a file on the appliance and
  asking this tree for it is asking the wrong machine.

* **And the same for the documents, which is where the pointers actually
  rot.** `docs/` and the wiki send a reader to 330 paths under this
  repository's own directories. Nothing checked that any of them still
  resolves, so a file renamed in one commit leaves every document that named
  it pointing at nothing, silently, until somebody follows the pointer.

  Sound for the same reason: the holder is the filesystem and nothing reads
  what is at the other end. The rule that makes it usable is that the first
  path component must be one of this repository's own directories, and that
  rule replaced an allowlist. Measured before it was written: across `docs/`
  alone, 280 mentions gave 4 misses, and **3 of the 4 were VPP's own headers**
  (`vnet/error.h`, `vnet/ethernet/node.c`,
  `vnet/ipfix-export/ipfix_info_elements.h`), which are real files on a box and
  not files here. The fourth was a wiki page named relative to `wiki/` instead
  of the root. With the rule: 330 mentions, 0 missing. An allowlist of three
  would have been an allowlist somebody grows.

  **The rule has one holder**, `test/doc_paths.py`, because both checks ask the
  same question. Written out in each of them it took one commit to disagree:
  the changelog copy did not know that `vnet/error.h` is VPP's header rather
  than ours, and went red on the entry you are reading, whose whole subject is
  that distinction. Breaking the holder turns both readers red at once, which
  is the point of there being one.

* **The release gate passed a medium that had failed its own self-test**
  ([#311](https://github.com/andrediashexa/hexarouting/issues/311)).
  `appliance/selftest` can end with four sentences and one of them is
  `N check(s) failed. The box is NOT ready.`. Every reader on the gate path
  asked `said_a_verdict()`, which is true of that sentence, so
  `tools/release-gate.sh` and `lab/boot-iso.sh` accepted such a medium and the
  gate reprinted that line underneath its own `is ready to publish`.

  `said_a_good_verdict()` has been in `tools/selftest-verdict.sh` since that
  file was written, for exactly this, and **nothing called it**. The gate's own
  error text names the failure it was written for - "reached no verdict" - and
  the absence of a verdict was never the dangerous case.

  Three things, because it is one defect wearing three coats:

  * the gate and `lab/boot-iso.sh` demand a **good** verdict now, and a failed
    one is reported as a failed medium with its `[FAIL]` lines;
  * `lab/install-iso.sh` did catch it, by spelling `check(s) failed. The box is
    NOT ready.` **by hand in two places**. It reads `said_a_failed_verdict()`
    from the holder instead, which is a separate reader from "not good" because
    a console with no verdict at all sends the reader to the bootloader while
    a box that failed its checks sends them to the checks;
  * `test/test_selftest.sh`'s rule against a second speller covered the two
    sentences that mean the box is fit to hand over and **not** the one that
    means it failed, which is why it said nothing about those two lines for as
    long as they existed. The needles are derived from `VERDICT_RE` now, as the
    last sentence of each alternative. A rule about second copies that keeps a
    second copy of what to look for is the same defect wearing the check's
    clothes.

  And a new rule that would have caught it on the day: **every reader the
  holder defines has to have a caller.** A function nobody calls is the
  quietest way for a rule to stop applying, because the holder still reads as
  though it were enforced. It counts calls and not mentions, measured: the
  first version was satisfied by the comment that explains the defect.

* **`commit confirmed` took any number, and `0` looked like it had worked**
  ([#312](https://github.com/andrediashexa/hexarouting/issues/312)). `?` and
  the operator page have always said `<1-60>` minutes. Nothing enforced it, and
  the two ends of the range fail in opposite directions.

  Above it, `commit confirmed 600` armed a ten-hour window. The window exists
  so a box that lost its operator puts itself back while somebody is still at
  the console; ten hours is a box that reverts in the middle of the night, long
  after whoever made the change has gone home and concluded it held.

  **At zero it was worse, because it looked like it had worked.** `0` is falsy,
  so `if confirm_min:` never ran: nothing was armed, nothing was written to
  disk, no deadline was printed, and the box answered `commit complete`.
  Measured over a change to an interface description, on the shipped code:
  `SAID: ['commit complete']`, `PENDING: None`, change applied. An operator who
  asked for the protection, was told the commit had gone through, and had none.

  It took a non-access change to see it. A change to SSH or to accounts is
  forced into a ten-minute window whether or not the operator asked, so
  `confirmed 0` on one of those was quietly rescued by that path - which is why
  the first version of this test, written over an account change, could not
  tell the defect from the fix.

  Below zero armed a deadline already in the past.

* **`show interface` sent the operator to a command this VTY has not got**
  ([#308](https://github.com/andrediashexa/hexarouting/issues/308)). On a box
  whose NIC is bound to a driver DPDK can use and is still absent from the
  dataplane, the diagnosis ended `'show log' and the VPP journal say why the
  dataplane refused it.` There is no `show log`: the tree offers `show
  logging`, which is FRR's system log and says nothing about a NIC the
  dataplane refused.

  It was the only occurrence of that spelling in the product, and it printed on
  the one screen an operator reaches when the box forwards nothing **and** the
  binding is already right - so the next thing they type after reading it is
  refused. `appliance/selftest` reaches the same state and already names the
  journal properly; there is one account of it now.

  **And a check, because the holder was there all along.** Every command a
  message quotes is walked against the same command tree readline completes
  from, in both modes. Measured before it was written: 18 quoted commands
  across `hexash.py`, 0 rejected with the fix in place and exactly 1 with it
  taken back out, so there was no exception list to write. An argument stops
  the walk, because `commit confirmed 30` reaches a hint string rather than a
  dict and a check that demanded `30` be a tree key would reject every command
  that takes a value.

* **The medium was still told to reboot, by a second message**
  ([#310](https://github.com/andrediashexa/hexarouting/issues/310)).
  [#305](https://github.com/andrediashexa/hexarouting/issues/305) fixed the one
  `show interface` prints. `commit` after `dataplane interface <name>` ends with
  `reboot the box in a window you have chosen, and it comes up forwarding on
  that card`, which is right on a box with a disk and wrong on the one screen
  that says so itself, two lines under a banner reading *nothing here survives
  a reboot*. `say_dataplane_if_pending()` asked no live-medium question at all.

  On the medium the commit does not survive that reboot either, so there is
  nothing for the dataplane to come back to. It says that, and says to
  `install`.

  **And a comment that claimed something untrue, in the #305 fix itself.** It
  said `running_from_live_medium()` is "the test `appliance/selftest` already
  uses in four places". It is not the same test: the self-test asks
  `-d /run/live/medium || -f /run/live/overlay`, and this asks for the squashfs
  **and** an executable installer. They agree on every box either has been run
  on, and they are two tests - a root with the overlay and no squashfs is live
  to one and installed to the other. Neither state has been seen; saying they
  were one test would be the second copy this file's comments spend their
  length warning about.

* **Two screens promised a refusal the box stopped making**
  ([#309](https://github.com/andrediashexa/hexarouting/issues/309)). Since
  [#303](https://github.com/andrediashexa/hexarouting/issues/303) a box with
  only one card **accepts** `dataplane interface` for the card carrying the
  default route: it moves, and the management address goes with it onto the
  `linux_cp` pair. Two places still said otherwise, and both of them are read
  by an operator of exactly that box.

  The boot self-test ended its no-dataplane-interface warning with *"and so is
  the interface carrying the default route, so naming the management one
  cannot cut it off"*. `dataplane interface <TAB>` marked that card
  `carries the default route: refused`. So on a one-NIC VM - how most people
  meet this product - the completion told the operator their only card was
  refused, and then the box took it.

  The completion asks the same question the refusal itself asks: is there
  another card to name instead, read from the same `host_nics()`, so the hint
  and the answer cannot disagree about what the box has. The self-test states
  **both** cases rather than deciding which applies: working it out there would
  put a second copy of the VTY's rule in a shell script, and the two would
  disagree the first time one of them moved.

  No mechanical check on the self-test's wording, and the reason is the one
  `test/test_doc_copies.py`'s header already measured three times: comparing a
  message against the code that would produce it is unsound in both directions.
  The VTY half is the half a machine can hold, and it is held.

* **The trail's tamper check had no VTY spelling, and the wiki documented the
  dataplane's** ([#313](https://github.com/andrediashexa/hexarouting/issues/313)).
  The dataplane has had `show bpa pool history [verify]` since v0.23.0. The VTY
  proxied that command **without its argument**, so no VTY spelling reached the
  check, and two operator pages named the dataplane's own - which the wiki must
  never do, because it is written for people who have the VTY and not the
  dataplane's CLI.

  One of those pages puts it at the last step before the disk is erased, with a
  *pare aqui* attached to its result. An operator following it to the letter
  got a parser error at the moment they were told to stop if the answer was
  wrong.

  `show cgnat history verify` exists now and carries through. A third page's
  `show bpa timeouts`, which an operator also cannot type, is replaced by what
  they actually see: `commit` marks every value below its floor as the change
  is applied.

  **The check is narrow on purpose, and the width was measured.** Walking every
  command in every wiki code block against the tree flags 12 of 193 lines and
  **all 12 are false**: help-table rows with prose on the same line
  (`commit    aplica`), output lines (`commit complete`), a command from the
  installer's own tree, and commands with an argument. That is the rate
  `test/test_doc_copies.py`'s header records getting a check switched off. So
  the question asked has no heuristics in it: on a line that is a command
  alone, is the word after `show` one the VTY offers? 62 such lines, 0 unknown,
  and `show bpa ...` fails on the first word.

* **Three operator pages confirmed a port forward with a command that cannot
  print one**
  ([#314](https://github.com/andrediashexa/hexarouting/issues/314)). The
  *Como conferir* after `forward tcp 8080 to ...` said `show cgnat pool`.
  `format_bpa_pool` is 256 lines and prints the pool's addresses, blocks, mode,
  timeouts, ALGs and utilisation, and **no forwards**: they are in
  `show cgnat forward`.

  So an operator who set a forward and checked it the way the page said saw a
  pool that looked exactly as it did before, and had no way to tell a forward
  that took from one that did not. The reference page one directory over gets
  it right and even says this is the first thing to establish when somebody
  calls about port forwarding.

  The ALG recipe on the same page keeps `show cgnat pool`, because the pool
  view **does** print `alg`. Checked rather than assumed: changing both would
  have been the same mistake in the other direction.

  No mechanical check. Whether a command answers the question a page is asking
  is not something a machine can decide, and the sound narrow checks this
  project does keep - does the command exist, does the path resolve - would
  both have passed this page.

* **`TESTING.md` claimed a CI this repository has not got, and three numbers
  it stated as measured**
  ([#315](https://github.com/andrediashexa/hexarouting/issues/315),
  [#316](https://github.com/andrediashexa/hexarouting/issues/316),
  [#317](https://github.com/andrediashexa/hexarouting/issues/317)).

  *"CI runs level 1 on every commit."* `.github/` holds `ISSUE_TEMPLATE` and
  nothing else: no workflow, no run ever recorded. A reader takes that sentence
  for a safety net that catches what they forget, and there is nothing behind
  it. The document says there is none, and says what that means: every level is
  run by whoever made the change, which is why `tools/release-gate.sh` exists.

  Its first four commands include `sh tools/lab.sh hw-lab`, which has no host
  networking. Under `set -eu` that lab flushes and rewrites the **peer
  machine's** `ens19` over ssh first, then dies on the local `ip link set ens19
  up` it cannot run. The document's own hardware section has the right
  spelling, `make hw-up && make hw-lab`, three hundred lines further down.

  Three numbers stated as measured: *"36 labs"* where the tree has 62 files and
  the suite ran 87 lab runs; *"4 at a time"* where the default is a formula
  over `MemTotal` that yields 8 on this machine; and `ipfix no-exporter`
  turning *"19 of its assertions red"* where `lab/ipfix.sh` declares
  `# OFF-RED-AT-LEAST: 20` and `tools/run-all-labs.sh` **fails** an off run
  below its floor. So the document stated, as measured, a number the suite
  would refuse - and somebody reading it to judge an off run would have called
  a passing one a regression.

  Two checks, both with a real holder. The off-run count in the document is
  compared against the floor marker the suite enforces, number against number.
  And the CI sentence is compared against whether `.github/workflows` has
  anything in it: the first version of that one matched the very sentence that
  says there is no CI, which is recorded in the file rather than quietly fixed.
* **A sweep for claims this product makes and does not honour, and ten of them
  written down.** Not a fix: the deliverable is `docs/claims-sweep.md` and ten
  issues, each reproduced before it was filed. The shape swept for is the one
  that has now been found on a real box five times in two releases
  ([#303](https://github.com/andrediashexa/hexarouting/issues/303),
  [#305](https://github.com/andrediashexa/hexarouting/issues/305),
  [#306](https://github.com/andrediashexa/hexarouting/issues/306),
  [#307](https://github.com/andrediashexa/hexarouting/issues/307),
  [#295](https://github.com/andrediashexa/hexarouting/issues/295)): a sentence
  the box says out loud that it does not honour, in the first place an
  operator reads.

  Four are in what a box prints. `show interface` sends an operator to
  `show log`, which this VTY does not have and which occurs nowhere else in
  the product ([#308](https://github.com/andrediashexa/hexarouting/issues/308)).
  The boot self-test says the management card is refused, and since #303 the
  VTY accepts it on a box that has only one
  ([#309](https://github.com/andrediashexa/hexarouting/issues/309)). #305's
  contradiction survives in a second message, where committing a dataplane
  interface on the medium still says a reboot brings the box up forwarding
  ([#310](https://github.com/andrediashexa/hexarouting/issues/310)). And
  `commit confirmed 0` arms nothing, writes no record, prints no deadline and
  answers `commit complete`, while the help and the wiki both say the range is
  1 to 60 ([#312](https://github.com/andrediashexa/hexarouting/issues/312)).
* **The lab suite says which tree it measured, and refuses a verdict about
  two.** Every lab reads the repository as **it** starts, not once at the
  beginning, so a merge landing mid-run gives a summary that adds two different
  products together. `tools/run-all-labs.sh` has said so in its header for
  releases, and the rule was written down outside the repository too.

  It happened anyway, here, on the run before this release: the suite started,
  **eight merges landed into master over the next forty minutes**, three of
  them touching `lab/boot-iso.sh`, `lab/install-iso.sh` and
  `appliance/selftest` - the very files being measured - and the run reported
  `85 pass / 1 fail / 1 novpp`. Nothing in that output looked wrong, which is
  what makes it worse than a false `NO VPP`: the number was clean and meant
  nothing. It was caught by comparing commit times against the log by hand,
  before a release was cut on it.

  The suite records the tree it starts on and compares it at the end. A tree
  that moved prints what landed and **exits non-zero**, because counts that are
  about two products are not a verdict. The working tree is compared too, for
  an edit that lands without a commit.

  **Not a lock**, and that is the same argument the header already made: a lock
  is taken by whoever has the branch ready and the green suite in front of
  them. What is guarded is the verdict, not the merge - a run that reports its
  own validity is not something anybody has to remember.

* **Five things the three largest documents said and the code does not do**
  ([#318](https://github.com/andrediashexa/hexarouting/issues/318),
  [#319](https://github.com/andrediashexa/hexarouting/issues/319),
  [#320](https://github.com/andrediashexa/hexarouting/issues/320)).
  `docs/architecture.md`, `docs/routing.md` and `docs/logging.md` are 7,126
  lines and the previous sweep recorded, as its own gap, that nobody had read
  them end to end.

  **The commercial argument was stated two ways.** `docs/logging.md`'s opening
  said per-flow NAT costs `~2.4 GB/day`; its own volume table said `~1.6 GB`
  for the same 40 million records at the `~40 bytes` the page states as its
  premise, and 40e6 x 40 is 1.6e9. The wiki's table - the one an operator sizes
  hardware with - said `2,4 GB` directly under a premise line saying 40 bytes.
  The ratio between those two rows **is** the argument for BPA, and it read as
  30x in one place and 20x in the other.

  **A page described a refusal the box stopped printing that way.** Under *A
  route-map referenced and not defined*, `docs/routing.md` showed the message
  in four wrapped rows and then said it was reflowed, that `err()` wraps
  nothing, and that the box prints one line of 221 columns. Measured by calling
  `say_finding()`: four rows, widest **77**, inside the 79 budget - exactly the
  block above the sentence. The commit that made that change rewrote the width
  section the sentence points at and left the sentence.

  **Two numbers, each mirrored in a test comment beside an assertion that
  disagreed with it.** "four of the twenty-five statements are marked" where
  the assertion names three and the comment beside it says the other
  twenty-one are clean; and a statement quoted "at 66" columns that is 67,
  where the line the suite prints computes 87 against the comment's 86. And
  "all 38 suites" twice, where `test/suites.list` holds 54 - one of those was
  already wrong on the day it was written. Those two became "every suite",
  because the claim is that everything stayed green and the count goes stale
  every release.

  Three checks, each with a holder that is not prose. The volume tables are
  multiplied out against the bytes-per-record their own page states. A width a
  document claims for a line it quotes is measured off the quoted text. And the
  block `docs/routing.md` shows for that refusal is compared against the output
  of **calling** `say_finding()` - a fixture comparison and not a needle, which
  is the distinction that makes it sound where matching a document against the
  source that builds a message is not.

* **`docs/` was out of scope for the English-only rule, and three sections of
  `docs/routing.md` were in Portuguese.** `CONTRIBUTING.md` section 1 rule 1
  says documentation, source comments and VTY output are in English, and
  `test/test_english_only.py` holds it. It excused the whole of `docs/` for the
  sake of one file - `docs/wiki-audit.md`, which quotes the titles of a
  Brazilian-Portuguese wiki - and **a directory excused to accommodate one file
  is a directory nobody measures**, which is the same sentence as the roadmap
  section that said the installation work was empty and let
  [#303](https://github.com/andrediashexa/hexarouting/issues/303) through.

  Measured when it was brought back in: **33 lines** of real Portuguese prose
  in `docs/routing.md` - the `?`-inside-a-block section, the `show interface`
  section and the refused-interface-name section - and 14 in `wiki-audit.md`,
  every one of those a quoted wiki title. The 33 are translated. The one file
  is excused **by name**, with its reason, instead of by directory.

  Three identifiers joined the allowances and each is the same case the list
  already had for `TRANSITO`: `ASSINANTES PON-3`, `transito-primario` and
  `PL-MEUS-BLOCOS` are an interface description, a neighbour description and a
  prefix-list name that a Brazilian operator types, and what they type is what
  the box stores and reads back.

  The check's own rule that an allowance nobody needs is one nobody re-reads
  turns out to guard this from the other side: narrowing the scope back to
  where it was makes `ASSINANTES PON-3` unnecessary, and the file says so.





  Two are in the operator wiki. The audit-trail tamper check is documented at
  the last step before an irreversible disk wipe, and no VTY spelling reaches
  it ([#313](https://github.com/andrediashexa/hexarouting/issues/313)). Two
  pages confirm a port forward with `show cgnat pool`, which cannot print one
  ([#314](https://github.com/andrediashexa/hexarouting/issues/314)).

  Four are in the process. The release gate passes a medium whose self-test
  says `The box is NOT ready.` and reprints that under "ready to publish";
  `tools/selftest-verdict.sh` defines the reader that would catch it and
  nothing calls it, which is #307's asymmetry in the same two files
  ([#311](https://github.com/andrediashexa/hexarouting/issues/311)).
  `TESTING.md` claims a CI that does not exist
  ([#315](https://github.com/andrediashexa/hexarouting/issues/315)), opens
  with a command that rewrites a peer machine's NIC and then cannot run
  ([#316](https://github.com/andrediashexa/hexarouting/issues/316)), and
  states three numbers the code disagrees with, one of them in the direction
  that would take the release suite red
  ([#317](https://github.com/andrediashexa/hexarouting/issues/317)).

  What was swept and found clean is in the record too, because a section
  nobody measured reads exactly like a clean one, and that is how #303
  happened. No lab was run and no container was started: the full suite was
  running on another tree throughout.

## 0.74.0 - the installed box can be reached, and one card is enough

[#303](https://github.com/andrediashexa/hexarouting/issues/303), [#305](https://github.com/andrediashexa/hexarouting/issues/305).

### Added

* **A box with one NIC can forward, and the card takes its address with it**
  ([#303](https://github.com/andrediashexa/hexarouting/issues/303)). Measured
  on a fresh install on the maintainer's VM: one card, carrying the address and
  the default route, and `dataplane interface ens18` refused with "name the
  card carrying subscriber traffic instead". There is no other card. The
  advice could not be followed, so the box could not forward and could not be
  told to - and a VM with one virtual NIC is how most people first meet this
  product.

  The refusal's reasoning is right and it keeps its ground. A box with a
  second card is refused exactly as before, and the refusal now names the
  cards to use instead rather than describing them. What changes is the box
  where that advice names a card that does not exist: there the card moves,
  and the management address moves with it onto the `linux_cp` pair the
  dataplane keeps for the port, so the box is reached THROUGH the dataplane
  rather than beside it. The card keeps its MAC across the handover, so
  nothing upstream has to learn a new one.

  The operator types one statement. The address, the prefix, the MAC and the
  gateway are the box's own reading of Linux, written down as
  `dataplane management <card> address <a/len> mac <xx:..>` and a static
  default route, both visible in the candidate before the commit. A card whose
  address the box cannot read back - none, or two, or no gateway - is refused
  with the missing part named: an address it cannot see is one it cannot put
  back, and a handover with no way back is the walk to the console the
  original refusal was written to prevent.

  The record lives in `tune.env` beside the card, because between the commit
  that names the card and the boot that hands it over there is nothing on the
  box to read: the dataplane has no such interface yet and Linux still holds
  the address on a card that is about to stop existing. `apply-tuning` passes
  `--keep-management` to the tuner when it is set, and `bind-dataplane-nic`
  reads it before unbinding. All three places make the same decision from the
  same two facts - is there another card, and has the box written the way back
  down - and each checks both for itself.

  **The handover is a confirmed commit whether or not it was asked for**, for
  thirty minutes rather than the usual ten, because the window has to cover a
  reboot and a login rather than a reconnection. The box does not lose the
  session at the commit but at the restart after it, and if anything in
  between is wrong there is no session left to fix it from. An unconfirmed
  handover puts the configuration back AND restarts the box: the card was
  unbound at boot and the dataplane holds it, so `tune.env` going back only
  decides what the next start claims - and this runs on a box nobody can
  reach, where there is no session to ask. A revert that gives back no card
  restarts nothing, and neither does one whose card never moved: the window
  runs from the commit, so it can expire while the box is still on its old
  path, and the dataplane is asked whether it holds the recorded MAC before
  anything is restarted.

  `lab/one-nic.sh` measures it against a real dataplane: a box in a namespace
  of its own with one card, its address and its default route, the card handed
  to VPP, the product's own boot command replaying the saved file, and an
  upstream that then pings the box and opens a TCP session to it from off its
  own subnet. The bind cannot be performed in a container, so the kernel's
  loss of the port is modelled - the netdev is emptied, its name released and
  its ingress dropped - and the lab's negative check is what keeps that
  honest: with the card in the dataplane and nothing configured the box MUST
  be unreachable. Shown failing by making the model dishonest, where that one
  check goes red and every other check in the file stays green.

### Fixed

* **A box installed from the medium had no management network at all, and one
  typed at the console did not survive a reboot**
  ([#303](https://github.com/andrediashexa/hexarouting/issues/303)).
  Measured on a 0.72.0 appliance: `/etc/network/interfaces.d` empty,
  `/etc/network/interfaces` holding one line - `source
  /etc/network/interfaces.d/*` - and `networking.service` **enabled with
  nothing to do**. `appliance/hexarouting-install` contained zero occurrences
  of `/etc/network`. So the card came up `DOWN` with no address, the box was
  reachable at its console and nowhere else, and the `ip link` / `ip addr` /
  `ip route` the operator typed there went away with the power. It was
  escalated three times.

  The installer asks, with the other questions and before the first write:
  **which card**, and **how it is addressed**. The card is chosen from a menu
  showing what each one is - up or down, its driver, what address it is
  carrying this minute - because `ens18` and `ens19` tell nobody which cable
  is in which; the default is the card the medium is reaching the network
  through right now. The address answer is `dhcp`, an address with a prefix,
  or `none`.

  **DHCP is the default**, and the argument is the box this product is first
  met on: a virtual machine on a lab network has a DHCP server and no reserved
  address, and an operator who wants it to work and presses Enter has to get
  something that works. The operator racking an appliance has an address
  written down in front of them and types it, which is one answer either way.
  The cost of the default is stated rather than hidden - a box answering
  `dhcp` where nothing replies waits for the client to give up, and then says
  on its console that it has no address.

  **The medium carried no DHCP client.** `ifupdown` only *Recommends* one and
  `debootstrap` installs no Recommends, so `dhclient`, `udhcpc` and `dhcpcd`
  were all absent from the 0.73.0 squashfs: `iface ens18 inet dhcp` on that
  disk is a file that reads perfectly and does nothing. `build-iso.sh`
  installs `isc-dhcp-client`, and the install **refuses a disk** whose answer
  was DHCP with no client on it, so a default that quietly does nothing cannot
  ship again. For the same reason the resolver is written to
  `/etc/resolv.conf` and not as `dns-nameservers`, which is `resolvconf`'s
  option and this image has no `resolvconf`.

  `ifupdown` and not a second mechanism: it is what the image installs, what
  `networking.service` runs, and the only thing on the box that touches an
  interface. There is no NetworkManager, no `systemd-networkd` configuration
  and no netplan in the image, all three measured rather than assumed. The one
  thing missing was the file.

  **The answer is written even when it is `none`**, because without it a box
  whose operator chose no management network and a box installed by a medium
  that never asked are the same box on disk - and one of those has to be
  shouted about while the other must not.

  **The boot self-test reads that same file back on every boot** and reports
  what became of it: the address and the default route on a healthy box -
  which is what makes DHCP usable on a racked appliance, since the console is
  where the address it was given appears - and a failed check, naming the
  cause, for a box with no configuration at all, a card that is no longer
  there, a DHCP answer with no lease, or an address whose gateway did not take.
  That last one is the wrong answer rather than a missing one: `ifup` refuses
  a gateway outside the interface's own subnet and carries on, so the box comes
  up with an address, no default route, and reachable from its own subnet only.
  The installer refuses that pair at the one moment somebody can act on it, and
  the self-test says so at every boot if it ever happens anyway.

  **A box that is already racked is told.** The installer fixes every box
  installed from now on and does nothing for the ones in the field, which is
  the shape of [#248](https://github.com/andrediashexa/hexarouting/issues/248)
  and was left unsaid there for a release. A box upgraded with
  `appliance/install.sh` has no such file, so its next boot prints the failed
  check and the three commands that fix it from the console the operator is
  standing at - which is the only channel a box with no management network
  has.

  **The unattended path is unchanged and answers nothing new.**
  `hexarouting.mgmt-interface`, `.mgmt-address`, `.mgmt-gateway` and
  `.mgmt-dns` exist, and an install that passes none of them - which is every
  existing caller, the release gate included - takes DHCP on the default card
  and says out loud that it did.

  `lab/install-iso.sh` installs a box with the command line it has always had,
  boots it, reads the address off the kernel with nothing typed, and then
  **switches the machine off and starts it again** to answer the question the
  issue actually asks: not "is it reachable once", but "is it reachable
  tomorrow".

* **The roadmap said the installation section was empty of work, and a box
  installed from the medium has no way of being reached.** Measured on a real
  VM running 0.72.0
  ([#303](https://github.com/andrediashexa/hexarouting/issues/303)):
  `/etc/network/interfaces.d/` is empty, `appliance/hexarouting-install` writes
  nothing under `/etc/network/`, and `networking.service` is enabled with
  nothing to do. The NIC boots DOWN with no address and the `ip` commands typed
  at the console do not survive a reboot.

  The management address had been dropped from the installer's scope on the
  reading that it is "arranged by other means before the box is installed".
  That reading is wrong for the case everybody meets first: a VM created from
  the published medium, where there are no other means. Saying the section was
  empty is what let it go unmeasured.

* **The requirements page did not say how many network cards a box needs**
  ([#303](https://github.com/andrediashexa/hexarouting/issues/303)). The issue
  was met on a VM with one virtual NIC, which is how most people first meet
  this product, and the page somebody reads before buying hardware listed RAM
  and an instruction set and nothing about the network. The one resource the
  whole appliance exists for was the one the requirements did not mention.

  It now says one card is enough and what the box does with it, what a second
  card buys - not throughput, but a card for the refusal to name instead - that
  the card must be a PCI device because `lo`, a bridge, a bond and a veth have
  no PCI address and cannot be handed over, and that an IOMMU is what decides
  between `vfio-pci` and a `uio_pci_generic` with no IOMMU protection.

  No mechanical check was added, deliberately: the obvious one compares a
  message against the source that prints it, which `test/test_doc_copies.py`
  has already measured and rejected three times in its own header - 11 of 11
  false positives on one variant. There is no second written copy of this
  truth to compare against.


* **`bind-dataplane-nic` could not recognise a virtio management card**
  ([#286](https://github.com/andrediashexa/hexarouting/issues/286) again, in
  the one place it still bites). It compared the basename of the netdev's
  `device` link against the PCI address, and on a virtio guest that basename
  is `virtio3`. The comparison never matched, so the last line of defence
  against unbinding the card a box is reached over was not there at all - on
  exactly the machines this product is first tried on. It now walks up to the
  nearest PCI ancestor, the way the tuner already did.

* **On the installation medium, `show interface` told the operator to reboot,
  and rebooting the medium throws everything away**
  ([#305](https://github.com/andrediashexa/hexarouting/issues/305)). Two lines
  apart on the same screen: the banner said "nothing here survives a reboot",
  and the next command said "Reboot once the tuning has been applied and the
  pages reserved".

  The explanation decided from the startup file alone, and that file is
  identical on a medium and on an installed box's first boot. On the installed
  box the advice is exactly right: the tuner has asked for pages a reboot picks
  up. On the medium the dataplane is off deliberately and permanently, so the
  tuning is never applied and the reboot comes back to the same screen. The
  product already had `running_from_live_medium()`, in that same file, and the
  boot self-test uses it in four places to keep the two apart. This message did
  not ask it.

  **And nothing tested it.** `lab/boot-iso.sh` booted the medium and only ever
  read; the release gate read the self-test's line, which is correct on a
  medium and says nothing about rebooting. The one sentence that was wrong was
  the one nothing asked for. That lab now has a writer on the console, waits
  for the prompt rather than guessing when it arrives, asks `show interface`,
  and refuses a medium that answers with a reboot.

* **`lab/one-nic.sh` failed in the release suite and passed alone**, on one
  check and no other: the counter that says the packets came through the pair
  rather than past it. The ping in the same run reported 0% packet loss, so the
  packets had arrived and only the reading was early - the counter is the
  kernel's own on the TAP and moves as the dataplane punts, not as the reply
  leaves.

  It was a fixed `sleep 2`, which was enough alone at load 3.0 and not enough
  in a sixteen-way suite at load 4.5. A sleep long enough for the worst machine
  is a sleep everybody pays on every run and is still a guess about a machine
  nobody measured, so the reading waits for the counter to move instead, with a
  deadline. It can still fail honestly: packets that never arrive leave the
  counter where it was and the check goes red after the deadline rather than
  never.

## 0.73.0 - the box keeps the list of what was installed on it

[#302](https://github.com/andrediashexa/hexarouting/issues/302).

### Added

* **A box can say which of its own files are not from the version it claims**
  ([#302](https://github.com/andrediashexa/hexarouting/issues/302)).
  `show version` printed one line about a box made of about twenty files and
  could verify one of them. Measured before anything changed: a root stamped
  `0.70.0 (f10a3ed)` with v0.68.0's `hexarouting-tune` copied onto it produced
  a self-test report **identical line for line** to the same box untouched, 42
  lines each.

  `appliance/install.sh` records what it installed, with a checksum, as it
  installs it - `install` is a shell function wrapping the real one, so
  nothing is enumerated and there is no second list to fall out of date. The
  boot self-test checks it with `sha256sum -c`, and the exit status is the
  verdict: `<path>: FAILED` is coreutils' wording, not ours, and a needle for
  it would stop matching the day coreutils rephrases - silently, and in the
  direction that reports a broken box as a healthy one.

  The three files `install.sh` deliberately does not overwrite - `tune.env`,
  `vpp-base.conf` and `hexarouting.conf` - are named in the manifest with no
  checksum at all, because a checksum nobody may act on is an invitation to
  start acting on it. They go through one `keep()` that guards and records in
  one statement, and `test/test_install_manifest.py` refuses a second guard
  written by hand.

  It is a `[FAIL]` and not a warning: the version line is the first thing
  anybody reports on the phone, and when it is wrong every later diagnosis is
  against the wrong release notes. It stops nothing.

  31 files, 2.5 MB, **18 ms** at every boot on the machine this was measured
  on, and the suite measures and prints that on every run rather than leaving
  it as a claim.

### Fixed

* **Two more readers of the self-test's verdict were still spelling the old
  needle by hand**, in the two files where a false red costs most:
  `lab/boot-iso.sh` and `tools/release-gate.sh` itself. Both survived 0.72.0's
  two gate refusals by luck rather than by design - a LIVE medium takes the
  branch where DPDK is off deliberately, so it ends with a sentence the old
  needle still matched. Measured on the boot log of the gate run that passed:
  the live medium said `HexaRouting ready, with 2 warning(s) above.` and the
  installed box said `HexaRouting is installed and healthy, and has no
  interface to forward on:`. The first matches `HexaRouting ready`; the second
  does not.

  A live image that ever ended any other way would have had the gate refuse
  the release and name the wrong cause.

  The four sentences are in `tools/selftest-verdict.sh` now, sourced by all
  three readers, and `test/test_selftest.sh` refuses any of the three spelling
  them outside a comment. The gate is first in that list rather than last.

* **And the rest of that path was audited once, by hand**, so that nobody has
  to wonder how many more needles were waiting. Every literal string the three
  release-gate files look for in a console: 42, once the regexes and the labs'
  own probes are set aside. Nine do not appear literally in the product and all
  nine are accounted for in `tools/selftest-verdict.sh` - three belong to GRUB,
  `login(1)` and systemd, five are composed at run time, and one is a lab
  reading back its own configuration. Nothing else had gone stale.

  Recorded and deliberately not automated: nine findings and zero defects is a
  false rate somebody switches a check off over, and `test/test_doc_copies.py`
  already records the general version of that check being measured and rejected
  three times at worse rates.

* **The roadmap tool stopped reporting work that was not outstanding.** Four of
  its needles read as ordinary English about anything - `what remains`, `what
  is left`, `is missing`, `not yet` - and each had produced a finding about a
  sentence that was not a claim about work at all: a rule saying what remains
  can wait when a release is cut, a box that is not yet able to do its job, and
  a message that names which half of something is missing. Three false findings
  against five true ones is a tool somebody stops reading.

  Those four are attributed only within their own clause now. The five that are
  about work - `still open`, `remains open`, `still needs`, `why it waits`, and
  a row queued as pending - keep the fallback to the paragraph, because nobody
  writes those about a box or a message. The cost is a miss, shown rather than
  asserted: one of the weak four, with the issue number in the sentence before
  it, is no longer seen.
## 0.72.0 - the box said it was ready, and could not forward a packet

[#282](https://github.com/andrediashexa/hexarouting/issues/282),
[#291](https://github.com/andrediashexa/hexarouting/issues/291),
[#292](https://github.com/andrediashexa/hexarouting/issues/292),
[#293](https://github.com/andrediashexa/hexarouting/issues/293),
[#294](https://github.com/andrediashexa/hexarouting/issues/294),
[#295](https://github.com/andrediashexa/hexarouting/issues/295) (in part),
[#296](https://github.com/andrediashexa/hexarouting/issues/296),
[#297](https://github.com/andrediashexa/hexarouting/issues/297),
[#298](https://github.com/andrediashexa/hexarouting/issues/298),
[#299](https://github.com/andrediashexa/hexarouting/issues/299),
[#300](https://github.com/andrediashexa/hexarouting/issues/300),
[#301](https://github.com/andrediashexa/hexarouting/issues/301).

### Changed

* **A disconnected IPFIX collector was reported as connected**
  ([#300](https://github.com/andrediashexa/hexarouting/issues/300)). The
  pattern for it was a suffix glob: `*connected` matches `disconnected`, and it
  was tested first, so the arm that warns was dead code. A box whose retention
  records were spilling to disk because the collector was unreachable printed
  `[ ok ] TCP export 192.0.2.5:4739 connected` on its console.

  The state word is lifted out and matched whole. Four named arms and a
  default, and the `sed` in front of them was widened too: a word this file
  does not recognise has to arrive before any arm can report it, so the filter
  ahead of a `case` is part of the `case`.

  `connecting` gets words of its own rather than being folded into either
  neighbour, and that is a reading of `bpa_log_tcp.c` and not a guess:
  `bpa_tcp_disconnect()` does not clear `pending_connect`, so a handshake
  nothing answers leaves the box saying `connecting` while the exporter
  retries every five seconds, indefinitely. That is what a collector behind a
  firewall dropping the SYN looks like, and the records are not leaving the box
  in that state either.

  Nothing caught it because every fixture had the TCP transport disabled, so
  neither arm had ever run - which is
  [#298](https://github.com/andrediashexa/hexarouting/issues/298)'s argument one
  layer down.

* **`hexash --load` no longer refuses a box's whole configuration over a static
  route that is correct**
  ([#294](https://github.com/andrediashexa/hexarouting/issues/294)). The
  next-hop check asked the live VPP FIB what was connected, and at boot the FIB
  is mid-construction: the interface address that makes the next hop reachable
  is in the very file being read. `--load` refuses a file whole or not at all,
  so the box came up with no pools, no addresses and no attachments.

  What hid it is that `hexarouting.service` runs after `frr.service` and FRR
  already held the same route from its own `frr.conf`. A second copy of the
  configuration, in another daemon's file, was what kept the box configured -
  which left it one lost `frr.conf`, one conffile reinstall or one unit
  ordering change away from refusing everything.

  "What is connected" is now one set handed to one function, and the
  candidate's own interface addresses are unioned with the FIB's rather than
  replacing them on a path of their own. That is measured rather than
  preferred: the same refusal came out of an ordinary interactive `commit` that
  brings up an address and the route it makes reachable together, which is how
  an operator configures a new uplink. A union can only add prefixes, so
  nothing refused before stops being refused - a next hop on no connected
  network is still refused, at a configured box and at boot alike.

  The same file also refused a saved configuration naming the Linux side of a
  `linux_cp` pair, which the product creates itself from `linux-name` and which
  FRR configures by that name only. That one is worse in one respect: it is not
  gated on the routing having changed, so FRR's second copy did not hide it at
  all.

* **A pool that would translate the box's own BGP session was refused when an
  operator typed it and only logged when the same file was loaded at boot**
  ([#296](https://github.com/andrediashexa/hexarouting/issues/296)). Driven on
  a real dataplane, both doors, before anything was changed. Typed at a
  configured box the candidate is refused and no pool exists; loaded into a VPP
  with nothing on it the pool is created, the address is applied afterwards,
  and one `vlib_log_err` goes into a log nobody is reading.

  The plugin's check walks the LIVE interface addresses, and `--load` creates
  pools before it applies any, so at boot the walk finds nothing. That is the
  mirror image of
  [#294](https://github.com/andrediashexa/hexarouting/issues/294) and the
  dangerous direction: a false refusal is loud and immediate, a missed one is
  on the path that runs unattended.

  The VTY now refuses a candidate whose pool public range contains an
  interface address the same candidate configures, in one code path, so
  `commit`, `--load` and `rollback` agree. Every pool in the candidate and not
  only the one being added: at `--load` the distinction buys nothing, and at an
  interactive commit it is what stops a commit writing a file that `--load`
  will refuse whole months later.

  The plugin is untouched. Its live walk still catches an address that arrived
  from somewhere the candidate does not describe, and both fire together on a
  configured box.

* **The IPFIX boot marker was sent after the pin replay, and four places said
  it came before it.** `docs/logging.md`, the comment above
  `bpa_log_marker_emit()`, and the comments sitting on the two lines that arm
  it in `bpa_log.c` and `bpa_log_tcp.c` all say the marker goes out when a
  transport comes up, ahead of the blocks a pinned forward replays, so a
  collector knows which incarnation those records belong to. Since 0.67.0 it
  went out after them. That is [#217](https://github.com/andrediashexa/hexarouting/issues/217)'s
  argument read backwards: a record reaching a collector before this
  incarnation's marker belongs to the previous one as far as the collector can
  tell, and a box that has just rebooted announces every forward it carries
  ([#282](https://github.com/andrediashexa/hexarouting/issues/282)).

  Neither enable path could have fixed it: both can only arm the marker, since
  at that moment there is no `vlib_main_t` on the path and the block report's
  template may not have gone out. The ordering is decided in `bpa_log_drain()`,
  and the marker now goes out ahead of the queue there.

  `lab/ipfix.sh` reads the order off a real collector, which is the only place
  it is visible: the marker travels in its own report's buffer, so nothing
  inside the plugin shows the difference. It anchors the marker to the template
  announcement that opens the transition rather than to any earlier line,
  because markers repeat every thirty seconds and a marker from the previous
  transition would satisfy a looser check.

* **The boot self-test said "HexaRouting ready." on a box that had no
  interface to forward on**
  ([#297](https://github.com/andrediashexa/hexarouting/issues/297)). Measured
  on a real appliance: it printed `[warn] no dataplane interface: VPP was given
  no NIC to forward on` and then ended with the word "ready". `appliance/selftest`
  says why that matters two hundred lines above the summary it was thrown away
  by: "a box that forwards nothing looks like a box that is working".

  Warnings were never counted. `bad()` incremented a failure count and a
  warning was a bare `say`, so the summary knew only about failures and a box
  with five warnings ended identically to a box with none. That line is what
  survives: a console scrolls, a serial session is read from the bottom, and
  somebody racking a box reads the verdict rather than the forty lines above
  it.

  There are three outcomes under "nothing failed" now, because "installed,
  healthy, and not yet able to do its job" is a different thing from both of
  the others and is the ordinary state of a box on the day it is racked. It is
  not a failure and is not reported as one; saying "ready" of it was the part
  that misled. A live medium is unchanged, since DPDK is off there by design.

* **A pool on a box with no dataplane interface reported capacity for 504
  subscribers it would never serve**
  ([#299](https://github.com/andrediashexa/hexarouting/issues/299)). Measured
  on a real appliance whose dataplane held only `local0`: the pool committed
  cleanly, the boot self-test gave it a green tick, and `show cgnat pool` said
  `capacity 504 subscribers (usable 504)`. Nothing was attached to CGNAT, so
  no packet could reach it in either direction.

  `show cgnat pool` now prints the reason beside the figure, and names which
  half is missing, because an interface missing on the inside and one missing
  on the outside are different mistakes with different fixes. A pool with both
  sides attached is unchanged, under the same rule the worker-sharding caveat
  beside it already follows: a line that appears on a correctly attached box
  is one people learn to skip, and then they skip the real one too.

  **Not a refusal.** Writing the pools before the interfaces exist is ordinary
  staged work, and `hexash --load` applies them in that order at every boot, so
  refusing would refuse a box its own saved configuration. This is
  [#210](https://github.com/andrediashexa/hexarouting/issues/210)'s option 3
  and [#226](https://github.com/andrediashexa/hexarouting/issues/226)'s option
  A once more: the mechanism stays and the box stops claiming what it cannot
  do.

  The boot self-test's own green tick for the same pool is the other half, and
  it is fixed with it: `[ ok ] 1 CGNAT pool(s) configured` sat six lines below
  the warning saying the box had been given no NIC, and a reader scanning for
  green takes a tick as an answer. It says the count and the consequence now,
  as `[info]` rather than a second `[warn]`: the box is already warned about
  the cause and the summary names it, and a count an operator is meant to act
  on stops meaning anything when one cause contributes to it twice.

* **A refusal that comes back from the dataplane says when an upgrade is why.**
  Between `appliance/install.sh` finishing and the next reboot, the new control
  plane is talking to the dataplane the box was loaded with, and asking it to
  create a pool fails: the API message grew fields that build does not have.
  The refusal named `bpa_pool_add_del` and nothing else. It now says which of
  the two answered, names the build that answered, says an upgrade is the
  reason rather than the configuration, and states what clearing it costs -
  every session and every port block on the box - so the operator decides
  ([#293](https://github.com/andrediashexa/hexarouting/issues/293)).

  Only when the two builds actually disagree, and only when there is something
  to compare: a box running from a source tree, or without a version stamp, is
  told the comparison is not available rather than being handed a sentence
  about an upgrade that did not happen. `version_split()` is the one decision
  `show version` and the refusal both ask, so they cannot drift apart.

  Attached to refusals carrying a reason back from the dataplane, and to no
  others. A grammar error must not blame an upgrade.

* **`appliance/install.sh` says when it upgraded a box instead of installing
  one** ([#291](https://github.com/andrediashexa/hexarouting/issues/291) item
  3, which closes that issue). It behaved identically on a second run and
  closed with a first install's sentence - "Adjust /etc/hexarouting/tune.env
  and reboot so that isolcpus and hugepages take effect" - which an operator
  who tuned the box a year ago reads as nothing to do. It writes a new
  `bpa_plugin.so`, runs `daemon-reload` and `enable`, and restarts nothing.

  It reads the version stamp before anything is overwritten, and on an upgrade
  it names the version that was there and the one that is there now. On a box
  with systemd it also names which running services are still the older build
  and what restarting each costs: `vpp.service` picks up the new plugin and
  drops every session and every port block; `hexarouting.service` re-applies
  the configuration and drops none, and on its own leaves the split exactly
  where it was.

  **It still restarts nothing.** Restarting the dataplane unasked is worse than
  the split; the defect was that the split happened in silence. A stopped
  `vpp.service` gets a different paragraph, because telling an operator there
  is a split to clear when there is none sends them to book a maintenance
  window for nothing.

* **`appliance/install.sh` refuses a build it cannot install every component
  of, before it has written anything**
  ([#301](https://github.com/andrediashexa/hexarouting/issues/301)). It could
  not find a vat2 plugin, printed `warning: no vat2 plugin; the binary API
  will not be reachable` on stderr, wrote the new `bpa_plugin.so` over the old
  one, left the PREVIOUS version's vat2 plugin where it was, stamped the box
  with the new version and exited 0. The box then asserted a build it had half
  of, with a vat2 plugin older than the dataplane plugin beside it - the skew
  [#293](https://github.com/andrediashexa/hexarouting/issues/293) measured as
  unable to answer a message that grew a field.

  Nothing on the box could read the half that was wrong.
  [#291](https://github.com/andrediashexa/hexarouting/issues/291)'s `show
  version` compares the stamp against the build the loaded plugin reports and
  both of those were the new version, and the self-test asks whether the
  binary API answers, not which build is answering.

  Both plugins are now resolved at the top of the script, before the first
  byte is written, and a missing one is refused there. A box whose upgrade is
  refused is still wholly the build it was that morning, which is the only
  outcome an operator woken at two in the morning can act on. The
  `bpa_plugin.so` refusal moved up with it: it used to sit past the whole of
  `src/hexash`, so a tree with no dataplane plugin left a box carrying a new
  VTY over an old dataplane.

  **The same on both paths**, because what is refused is a property of the
  build tree and not of the root being written. A `$DESTDIR` fakeroot gets no
  old vat2 plugin left behind, it gets none at all - and the medium assembled
  from that tree would carry the missing half to every box installed from it,
  which is what
  [#119](https://github.com/andrediashexa/hexarouting/issues/119) cost.
  `appliance/build-iso.sh` already refused both plugins before the
  debootstrap; this is the first of those guards inside the script the others
  were watching.

* **The product stopped claiming an upgrade path its own users cannot run**
  ([#295](https://github.com/andrediashexa/hexarouting/issues/295)). Measured
  on a real appliance: there is no `install.sh` on it, no source tree and no
  toolchain. `appliance/install.sh` says at the top of the file that it is the
  path "for updating a box in production without reinstalling it", and that is
  true of the release gate and of `lab/upgrade.sh`, which have a build tree.
  It is not true of anybody holding a published medium, whose only route to a
  new version is a reinstall that erases the disk - and with it the volume
  holding, in dynamic mode, the connection record the Marco Civil requires for
  a year, and in deterministic mode the configuration trail that says which
  pool definition was in force at a past instant.

  Nothing was deleted: the mechanism is real and works for the person it works
  for. Five sentences now say who that is, and the Portuguese twins of three
  of them moved with their originals. The wiki has a page on taking a new
  version, written for somebody holding a medium, which says what the
  procedure is today, what it destroys, what has to be outside the box before
  the window opens, and in what order.

  The mechanism that would make an in-place upgrade possible is not chosen
  here and is not this entry's subject.

  `test/test_doc_copies.py` now refuses a document that quotes
  `appliance/install.sh`'s opening sentence and gets either the words or the
  line number wrong. Two files quote it and cite a line, and this change
  rewrote that header, which is exactly the day a quotation rots.

### Added

* **The merged-issue check understands a release being cut.** It compared the
  merges since the last tag against `## Unreleased` alone, and a release is cut
  in two steps: the heading is renamed to the new version, and the tag is
  created after it. In between, `git describe` still answers the previous tag,
  every merge since it is legitimately under the new version's heading, and
  `## Unreleased` is correctly empty. It reported all twelve of this release's
  issues as unwritten, at the one moment somebody is reading this file hardest.

  It reads `## Unreleased` and the section named by `VERSION` when that is not
  the tag `git describe` answers. Both and not either: a branch may merge after
  the rename, and its entry belongs in the open section.

* **Every change a commit can make now has to need a capability.**
  `cap_needed()` answers `CAP_ALL` for anything in `DELTA_ADMIN`, `CAP_CGNAT`
  for anything in `DELTA_CGNAT`, and `None` for a delta that changes nothing -
  and the caller reads `if need and not self.require(need, "commit")`, so
  `None` is not "refuse", it is "do not ask". That is right for an empty delta,
  and it means a key `config.py` produces that neither list names falls through
  to no check at all: a cgnat-operator account commits a change that should
  have needed an administrator, with no refusal and nothing in the trail,
  because no check ran.

  Measured: 36 keys, 36 covered, no gaps and no ghosts. This is a guard rather
  than a repair, and what it guards against is a configuration kind added by
  somebody who did not know the second file existed.

  The smaller list is also frozen by name, because coverage cannot see the
  other failure: moving a key from `DELTA_ADMIN` into `DELTA_CGNAT` is a grant
  that reaches every cgnat-operator account on every box at the next upgrade,
  and the key is still covered. There is no independent source to derive that
  list from - which changes a CGNAT operator may make **is** that tuple - so
  frozen is the honest shape, in the way an `OFF-RED-AT-LEAST` floor is:
  widening it is one line, and the line is in a test, where it needs a reason.

* **A self-test fixture that passes, which the suite had never had**
  ([#298](https://github.com/andrediashexa/hexarouting/issues/298)). It
  arranged 105 boxes and every one of them failed something - its own count
  line said `105 of 105` - so the summary every healthy appliance in the field
  prints had never been executed, and the assertion written for
  [#297](https://github.com/andrediashexa/hexarouting/issues/297) passed for
  the wrong reason and stayed green under both mutations of it.

  `healthy()` arranges a box the self-test finds nothing wrong with at all: 30
  checks green, no `[FAIL]`, no `[warn]` and no `[info]`. Three of #297's four
  source-reading assertions are gone, replaced by measurements through a box -
  the ready line, the warning count, and the sentence a box gets when it cannot
  forward each go red now when the summary is changed. The fourth stays, and
  its own section says why no box can answer it: a warning added later with a
  bare `say` still prints, and is missing only from a count the box has no
  independent opinion about.

  Two things had to be satisfied that are worth recording. The `mkcpus` pattern
  almost every existing fixture uses leaves exactly one control-plane cpu,
  which is a warning in its own right. And `hexash` is not installed on a build
  machine, so all 105 existing boxes print `[FAIL] hexash is not installed`;
  the stub for it is created inside the new section and removed at the end,
  because putting it in the shared `$BIN` would have silently repaired a failed
  check on every box above it.

* **Every issue a merge closes now has to be written down here.** Two of the
  five merges since 0.71.0 landed with no entry at all, and the gap was found
  by reading the section rather than by anything failing. A release note is
  written from `## Unreleased`, so an entry nobody wrote is a change the reader
  is never told about. `test/test_changelog_landing.py` compares the issue
  numbers in the merge subjects since the last tag against the links under that
  heading.

  Offline, against git and this file and nothing else, which is the same line
  `tools/roadmap-state.sh` sits on the other side of: `make test` has to work
  with no network. A merge that names no issue is not a finding - a chore
  branch legitimately closes nothing, and demanding a number would teach people
  to invent one.

* **An upgrade lab: nothing in this repository had ever put one version of the
  product on top of another**
  ([#291](https://github.com/andrediashexa/hexarouting/issues/291)).
  `appliance/install.sh` says at its own line 4 that it is the path "for
  updating a box in production without reinstalling it", and five places in
  the product assert that doing so is safe. None of them had a red light
  behind it. `lab/upgrade.sh` compiles `v0.68.0`'s dataplane plugin, runs that
  tag's own `install.sh`, configures a box an operator would recognise through
  the VTY that install put there - a CGNAT pool with a block size and a
  timeout, both interfaces attached, a static port forward, addresses, a
  static route, `save`, and three rollback revisions - then runs the current
  tree's `install.sh` over it and asserts the box is **still configured**: the
  file the old version saved is accepted whole by `hexash --load`, the pool is
  in the dataplane, the forward still forwards a real packet, the old
  version's revisions still load into the new VTY, and the configuration trail
  was appended to rather than restarted. Removing one statement's spelling
  from the new grammar takes 17 of its 35 checks red, which is the hazard the
  issue is about: `--load` refuses the file **whole**, so a statement the new
  VTY does not accept does not degrade an upgraded box, it unconfigures it, at
  a boot nobody is watching.

  Two things it measured on the way, neither of which it asserts, because both
  are defects somebody is going to fix and an assertion would go red on the
  fix. First, the installer replaces `bpa_plugin.so` under a running VPP -
  `/proc/<pid>/maps` shows the mapping `(deleted)` - and restarts nothing, so
  between an upgrade and the next reboot the control plane is new and the
  dataplane is old. In that window the new VTY changes a setting on an
  existing pool normally and **cannot create a pool at all**: `error: pool
  'x': Call failed: bpa_pool_add_del`, because the API message grew fields the
  running plugin does not have. Second, `hexash --load` refuses the whole file
  for a **correct** static route when FRR is not already holding it, because
  the next-hop check reads the live VPP FIB and the addresses that make the
  next hop reachable are in the file being loaded. A normal boot survives that
  only because FRR read the route out of its own `frr.conf` first.

* **The release checklist now says to compare the roadmap against the
  tracker.** `tools/roadmap-state.sh` shipped with
  [#292](https://github.com/andrediashexa/hexarouting/issues/292) and the only
  place that said to run it was the roadmap itself. Cutting a release is the
  moment somebody reads the roadmap in order to write a release note, so a
  checklist that does not mention it is a second copy of the release procedure
  disagreeing with the first by leaving something out. It is item 6 and it
  blocks nothing: what it finds is either a sentence to move into the past
  tense or an issue that needs a home in the file.

  `test/test_doc_copies.py` now refuses a counted list whose count is wrong.
  That sentence opens "produce all six of" and the number is the copy a reader
  trusts: somebody who has done five things and read "all five of" stops, and
  the item that does not get done is the last one. The count had to be changed
  by hand when this item was added, which is the argument for checking it.

### Fixed

* **The release gate refused this release over a box that had reported itself
  correctly.** `lab/install-iso.sh` asserts that an installed box says what it
  is before it offers a login prompt, and it anchored that on the literal
  `HexaRouting ready.` - the last line the self-test printed, which made it the
  right anchor for an ordering question. #297 gave that summary four forms, and
  a box with no NIC to forward on - which is every box this lab builds - ends
  with a different sentence. The report was complete, in the right order, and
  the gate refused the medium.

  The ordering check anchors on the row of equals signs the self-test writes on
  every path, which cannot be changed by a verdict being reworded. What the
  verdict SAID is a separate assertion now, and it accepts any of the four,
  because demanding "ready" would demand the box lie about a NIC it was never
  given.

  `test/test_selftest.sh` compares the two files: every sentence the self-test
  can end with has to match the pattern the lab reads verdicts by, and that
  pattern has to be the only place in the lab those sentences are spelled.

  The first version of that check was not enough and the gate said so. It
  asked whether the lab knew each sentence somewhere, and the lab did - in the
  assertion that had just been fixed. Four hundred lines below it a second
  reader went on deciding whether the box had come up at all by grepping for
  `HexaRouting ready`, so a box that printed a complete report was reported as
  a box that never booted, and the gate refused the medium a second time for
  the first time's reason. It found a fifth
  defect while being written - the lab knew three of the four, and `ready,
  with N warning(s) above` is not matched by a needle for `ready.`, so a
  healthy box that carried one warning would have failed the gate too.

* **The release gate refused a release whose note was correct.** It required
  "built", "booted", "installed" and "again" on one LINE, and this release's
  note wrapped that sentence at a sensible width, so the gate read two lines
  with two words each and refused. It reads one paragraph now.

  It is the needle trap this repository has met six times, and it is worst
  here: everywhere else it costs a false green, and on this gate it costs a
  false red on the one thing that has caught every release mistake this
  project has made. A gate that refuses correct work is a gate somebody learns
  to argue with.

  A paragraph and not the whole note, because the objection in the sentence it
  replaces still holds: matching across everything would pass on a note that
  mentions an installed box three paragraphs away for some other reason, which
  every note about this product eventually does.

* **`lab/upgrade.sh` would have been NOT MEASURED in every release suite it
  ever ran in.** Git inside the lab container refuses the bind-mounted
  repository outright - `fatal: detected dubious ownership in repository at
  '/src'`, because the container is root and the tree is the operator's - so
  the `git archive` the lab extracts the old release with never answered, on
  any machine, in a clone as much as in a worktree. It fell back to a cache
  somebody had to populate by hand, and without one the verdict was NOT
  MEASURED: a lab reporting nothing wrong while asserting nothing at all.
  Measured in a fresh clone, which is what the release suite runs from: NOT
  MEASURED before, 35 checks and LAB PASSED after.

  Fixed with a one-shot `git -c safe.directory=/src` rather than a config
  write. The container is thrown away so the global file would cost nothing
  here, but [#220](https://github.com/andrediashexa/hexarouting/issues/220) is
  what the habit costs: 2325 copies of one line in a `~/.gitconfig` this
  product does not own.

  The message blamed the wrong cause, which is how this survived being
  written. It named a git worktree and a missing tag, and both were innocent.
  It now prints git's own line and says what each of the two remaining causes
  means, neither of which can be fixed from inside the container.

* **A lab whose fixture was never built reported the product as broken twenty
  ways.** `sh tools/lab.sh upgrade v0.40.0` closed `LAB FAILED: 20 check(s)`.
  That tag's VTY refuses the configuration the lab types - "pool 'minas':
  inside and outside required", a grammar that has moved since - so the old
  box was never configured and there was no upgrade to measure at all. The two
  preconditions that would have said so were evaluated at the verdict, after
  every assertion had already been made, and a FAILED outranks a NOT MEASURED:
  the run said the product was broken about a box that had never existed.

  They now stop the lab where they are known, alongside the extraction and the
  compile, and the message quotes what the old VTY actually said. Measured:
  the same command now closes `LAB NOT MEASURED: 1 check(s)` with nothing red,
  and `sh tools/lab.sh upgrade` is still `LAB PASSED`.

* **The roadmap's "what is blocked on somebody else" table listed two things
  that were not blocked**, and that table's own opening sentence is the
  promise it broke: "Kept short and kept honest, so the rest of this file is
  read as work that can actually start." The soak test shipped in 0.71.0 and
  is called delivered in three other places in the same file, while the table
  went on listing it beside the two issues that really do need a NIC - and the
  soak needs none, which is the first argument of its own section ninety-six
  lines further down. The commit read-back promotion was listed as pending
  after it had shipped and its issue had been closed. Both rows are gone.

  `test/test_doc_copies.py` now refuses a document that calls an issue blocked
  in that table and delivered anywhere else in itself. It is the only half a
  test with no network can see: an issue simply closed, with nothing in the
  file saying so, is invisible to it, and the second row was exactly that.

## 0.71.0 — the box waited for a page size its CPU cannot have, and told the operator to reboot

[#30](https://github.com/andrediashexa/hexarouting/issues/30),
[#226](https://github.com/andrediashexa/hexarouting/issues/226),
[#248](https://github.com/andrediashexa/hexarouting/issues/248),
[#279](https://github.com/andrediashexa/hexarouting/issues/279),
[#289](https://github.com/andrediashexa/hexarouting/issues/289),
[#290](https://github.com/andrediashexa/hexarouting/issues/290).

### Fixed





* **A box whose CPU cannot do 1G hugepages never left the first-boot profile,
  however many times it rebooted**
  ([#290](https://github.com/andrediashexa/hexarouting/issues/290)). Measured
  on the maintainer's own box, and it is why that machine never got a dataplane
  card: DPDK was off, so no NIC could be claimed, whatever the card was. It is
  why switching to `vmxnet3` changed nothing.

  `grep -c pdpe1gb /proc/cpuinfo` is **0** there, so the kernel has no
  `hugepages-1048576kB` pool and silently ignores `hugepagesz=1G hugepages=5`
  on the command line. The tuner read that one path, got 0 on every boot,
  never set `tuned_now`, and emitted the conservative profile with
  `plugin dpdk_plugin.so { disable }` - for the life of the box. It printed
  `(no 1G support)` and told the operator to reboot.

  Meanwhile VPP's own shipped `/etc/sysctl.d/80-vpp.conf` had reserved
  **2 GB of 2 MB pages**, sitting unused.

  The tuner now uses the largest page size the box actually has, reading the
  pools that exist rather than the one it would prefer. 2 MB is not a degraded
  mode: VPP runs on it, and 1 GB is a preference about TLB entries - one entry
  against five hundred and twelve - not a requirement. A box with both pools
  still gets 1 GB, and a box with neither stays conservative rather than
  claiming a size it has not got.

  The fixture could not build the failing box either: it always wrote a
  `hugepages-1048576kB` pool, so every assertion about the profile was made on
  hardware that works. Two of the three mutations stayed green until it could
  build a box with both pools and the page COUNT was asserted - asking for 6
  on a 2 MB box reserves 12 MB rather than 6 GB, while every other number in
  the profile assumes the gigabyte figure.

* **A box whose only NIC is its management path was told to reboot, and the
  reboot could not end anywhere**
  ([#289](https://github.com/andrediashexa/hexarouting/issues/289)). Reported
  from a VM with one virtio NIC: `show interface` said "DPDK is off until
  hugepages exist ... Reboot once the tuning has been applied", and after that
  reboot the next screen says no interface was named, and naming the only one
  gets it refused because it carries the default route
  ([#123](https://github.com/andrediashexa/hexarouting/issues/123)). Two
  reboots and a naming attempt to learn the machine cannot forward until it has
  a second NIC.

  `show hardware` already said the important half, marking the card as refused,
  but nothing sent the operator there from the screen they actually opened.

  The first-boot explanation now says it, **only when it is true**: that there
  is no NIC this box could be given, which one is refused and why, and that a
  reboot does not change it. A box with a spare NIC is told nothing extra,
  because a line printed on every boot is one nobody reads. A box Linux shows
  no PCI NIC at all gets its own answer rather than being told to add a second
  one.

  It reads `host_nics()` and `default_route_nic()` - what `show hardware`
  prints and what the tuner decides on - so it cannot call a card eligible that
  they would then refuse.

* **Nothing tested the assertions every lab's verdict is made of.**
  `lab/verdict.sh` defines `expect`, `expect_in`, `expect_said`,
  `expect_not_said`, `expect_timed` and `verdict` itself. Each lab is judged by
  them, the suite by the labs, and a release by the suite - so a helper that
  silently always passed would make every lab using it green for the wrong
  reason, invisibly at every level above it.

  Not hypothetical: three needles broke in one day because a message was
  reflowed ([#279](https://github.com/andrediashexa/hexarouting/issues/279)),
  and one was an `expect_not_in` that then passed whether the box said the
  thing or not. `expect_said` and `expect_not_said` were written for that and
  nothing held them to it either.

  `test/test_verdict.sh` asserts the COUNTER rather than the words: a helper
  that prints `[FAIL]` and forgets to raise `VERDICT_FAILURES` is a lab that
  prints red checks and exits 0. Each case runs in its own shell, because the
  counters are globals and a leaked one would make the next case pass on the
  previous one's result.

* **A fixture the box could not build was reported as a product failure.**
  `lab/pool-del.sh` fills two session tables and times a barrier across them.
  In a release suite it reported `[FAIL] the large table was several times it`
  and passed alone minutes later at 10000 and 50000 sessions: the box was
  filling two tables while three other labs ran. Nothing about the barrier
  cost, which is what that lab is for, was wrong or even read.

  `expect_setup` says NOT MEASURED for a precondition. It is **not** a pass -
  the lab still exits 2 and the suite counts it - but it stops a release being
  blamed on the product for a machine that was busy. Only for the fixture:
  whether the box can create a session at all is another lab's subject, and
  using this for the thing under test would be a check that cannot fail.

* **The tuner's fixture only ever built the NIC shape that already worked.**
  `appliance/test_tune.py` symlinked `/sys/class/net/eth0/device` straight at
  the PCI device, which is the bare-metal layout - so every assertion about the
  generated `dpdk` stanza was exercised on the one shape where taking the
  basename happens to be right, and #286 could sit in the tuner with a green
  suite.

  The fixture can now put a bus device in the way, which is what every virtio
  guest looks like, and the stanza is asserted end to end on both shapes.
  Asserting that `nic_pci()` resolves the address is not the same claim: a
  resolver that works and a stanza that still says `no-pci` leaves the box
  exactly as broken, and the red prints that stanza.

* **Two places said the reserve block "has to land" on the subscriber's own
  public address, and the code says the opposite in as many words**
  ([#226](https://github.com/andrediashexa/hexarouting/issues/226)).
  `bpa_dyn.c`, where the choice is actually made, says *"A PREFERENCE and not a
  rule"* and gives the reason: the reserve is sharded by index, so the blocks
  on a subscriber's own address are not all in its worker's slice and the ones
  that are may be held. Refusing the extension rather than taking a block
  elsewhere would turn a property about layout into a customer losing traffic.

  `bpa_pool.h` and `docs/architecture.md` both stated it as a requirement. That
  is a guarantee the product does not make, in the one place a reader looks to
  find out whether it does - and RFC 4787 REQ-2 is exactly the kind of claim
  somebody quotes in a tender.

  `docs/architecture.md` states the same property correctly a thousand lines
  further down, and the roadmap and the operator wiki both had it right, which
  is what made the two wrong copies findable at all.

  No mechanical check is added. Comparing a sentence against the code that
  implements it was measured and rejected three times this cycle and is
  recorded in `test/test_doc_copies.py`: line-split literals give false
  negatives and run-time composition gives false positives, and a check that
  goes red on work that is right is the worst kind.
### Added

* **The boot self-test says when the box's ssh host keys were handed to it
  rather than generated on it**
  ([#248](https://github.com/andrediashexa/hexarouting/issues/248)).
  Every medium published up to and including 0.66.0 carried the host keys
  openssh-server's postinst generated inside the build container, private
  halves included, and `hexarouting-install` unpacked that squashfs onto the
  disk before enabling sshd. So every appliance installed from one release
  answers with the same host key and its private half is in the download.
  [#245](https://github.com/andrediashexa/hexarouting/issues/245) removed the
  keys from the image and made the installer generate a set per box, which
  fixes every install from 0.67.0 onwards and does nothing for a box already
  racked. Those boxes are still answering with it, and until now nothing told
  their operators.

  ```
    [FAIL] the ssh host keys were not generated on this box
          They are older than the filesystem they sit on, so they came in with
          the system image. Every box installed from that medium answers with
          this same key and the private half of it is in the published download:
          an ssh session to this box can be impersonated, and the client will
          not object, because the fingerprint it pinned is the fingerprint it
          was handed.
            ssh_host_ecdsa_key ssh_host_ed25519_key ssh_host_rsa_key
          regenerate them, at the console:
            rm /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub
            ssh-keygen -A
            systemctl restart ssh
          Every client that pinned the old fingerprint will then refuse to
          connect and report that the host key changed. That is this being
          fixed, not an attack, and it is the cost of fixing it.
  ```

  A box whose keys are its own gets the other line and nothing else:

  ```
    [ ok ] the ssh host keys were generated on this box, not handed to it
  ```

  **The signal is the timestamp, and deliberately not the key's comment.**
  `ssh-keygen -A` stamps `<user>@<hostname>` into the comment, and the hostname
  is the one `gethostname()` answers, which `chroot` does not change: the
  installer runs `chroot "$MNT" ssh-keygen -A`, so a key a box generated for
  itself carries the **live medium's** hostname, measured as `root@hexarouting`
  whatever the operator named the box. A comment-against-hostname check would
  report every correctly installed box with a name of its own, and the
  installer would be the thing making the evidence. What separates the two
  cases is that a file cannot have contents older than itself: `unsquashfs`
  restores the mtime the build container wrote onto an inode created at install
  time, so an image key's mtime is earlier than its birth time and a key
  generated here is not. Measured on the published 0.37.0 medium, whose keys
  are stamped three minutes before its squashfs was made. It costs nothing kept
  in the tree, which a list of the fingerprints each release shipped would not
  and which is the opposite of what #245 just did.

  Not blind in either direction: a box with no host keys at all is a failed
  check saying sshd cannot start, a key whose birth time the filesystem does
  not record is reported as undecidable rather than as fine, and the live
  medium - which carries no host keys on purpose - is not failed for it.### Added

* **A soak test: hours of session churn, and what does not come back is a
  leak** ([#30](https://github.com/andrediashexa/hexarouting/issues/30)).
  Every other lab in this repository finishes before the first timeout
  expires, so none of them can see the failure that reaches production: the
  box that works for a week and then stops. `lab/soak.sh` runs for as long as
  its argument says, and the argument is the only one it takes.

  ```
    sh tools/lab.sh soak            210 s of churn, about four minutes in all
    sh tools/lab.sh soak 28800      eight hours of it
  ```

  **Every cycle fills the table and every cycle empties it**, and that shape
  is what makes the memory question answerable at all. Fifteen seconds of
  traffic — 32 subscribers, 199 source ports each, at a rate that fills the
  whole burst so sessions are created while others time out — then twenty
  seconds of nothing, which is longer than the UDP timeout and the block
  hold-down together. The lab reads the box at the end of each half. **Every
  trough is the same state of the box**: no session, no block held, nothing
  arriving. So two troughs are directly comparable, and anything a later one
  holds that an earlier one did not is memory the box did not give back —
  with no working set to subtract, no settling to wait out and no allocator
  wobble to make room for.

  **That shape was arrived at by measurement, and the first one was wrong.**
  The first version ran a 12 s burst inside a 15 s cycle against a 20 s
  timeout, so the tail of one burst was still alive when the next began. Over
  60 cycles the live session count never settled: it climbed by about 21 a
  cycle for 21 cycles, dropped by 511 in one, and began climbing again — a
  beat between the idle gap and the timeout, with the heap following its
  high-water mark upward in steps for five and a half minutes. **Nothing was
  wrong with the box.** A load that never reaches a steady state cannot be
  asserted to plateau, and a lab that asserted it anyway would have been
  reporting its own generator as a product defect. Filling the cycle removes
  the gap the beat needs.

  What it asserts, and every one of them against a number the box printed:
  the session table is back at the baseline **at every trough**, not only at
  the end; every session the box created, the box expired; every block came
  back to the pool at every trough and the box's own allocation and release
  counters end equal; blocks were already being given back at the first
  trough, because a box that only released at the end would look the same in
  the closing numbers and be broken for the whole run; the heap and the
  resident size between two empty tables did not grow; and a real IPFIX
  collector on the far side of a veth counted one `natEvent` 17 for every
  `natEvent` 16, with **no allocation left open** — matched by port range and
  not merely by total — and nothing dropped on the way out.

  Measured on 2026-09-14 over 700 s: 127360 sessions created and 127360
  expired, 2532 blocks taken and 2532 given back, 2532 allocation records and
  2532 release records at the collector, and a trough heap that moved by
  **0.01 MB in nineteen comparisons** — one step of the smallest size the
  reading can show — with resident size unchanged. The 210 s default run, six
  cycles and 38208 sessions, moved neither:

  ```
    at the warm-up trough:  heap 238.37M, rss 446280 kB
    highest later trough:   heap 238.37M (+0.00M), rss 446280 kB (+0 kB)
    margins allowed:        heap 1.00M, rss 8192 kB
    troughs compared: 5, of which 0 held a session and 0 held a block
  ```

  **What a short run proves and what only a long one does** is arithmetic
  rather than a caveat, and the header states it in those terms. A leak is a
  rate, so what the memory assertion can see is its 1.00 MB margin divided by
  the sessions the run churned: 27 bytes per session over the 210 s default,
  0.2 bytes over eight hours, one byte in 3000 over a week.

  **It is not in `make test` and not in `tools/run-all-labs.sh`.** The suite's
  longest lab is about two minutes and this one is four at its shortest; a
  suite that waited on it would stop being run, which loses the suite rather
  than the lab. `NOT_LABS` carries that reason as data, so the run prints what
  it skipped before its first result.

  **Its control is not an off mode, and could not honestly be one.** There is
  no feature here to switch off, and a mode that made the box leak on purpose
  would be the lab performing the product's step in the other direction. Each
  assertion was instead shown red against a deliberately broken product —
  sessions that never expire, blocks that are never reaped, a `natEvent` 17
  that is never exported, a kilobyte leaked per translated packet, one
  exported record in a hundred dropped — and against three mis-set-up runs that
  turn the lab's own preconditions red, which is what those preconditions are
  for: a soak whose traffic missed the pool empties an empty table, gives back
  no block it never took, balances a log with no records in it, and reports a
  green run after eight hours. None of the mutations is left in the tree.

  **One of those controls found a defect in the lab and not in the box.**
  Breaking the heap parser on purpose — the shape a VPP release that renames a
  field would produce — left the check written to catch exactly that **green**:
  `awk` collapses an empty field, so every column after it shifted left, three
  assertions answered about the wrong number and the one guarding them said
  yes. The sample line carries a placeholder for a missing reading now, and
  the two memory deltas are computed with a guard, because under `dash`
  `$(( NA - NA ))` is zero and zero is inside any margin.

## 0.70.0 — a virtio NIC could never be the dataplane card, and a self-test that passed a box with none

[#8](https://github.com/andrediashexa/hexarouting/issues/8),
[#145](https://github.com/andrediashexa/hexarouting/issues/145),
[#163](https://github.com/andrediashexa/hexarouting/issues/163),
[#284](https://github.com/andrediashexa/hexarouting/issues/284),
[#285](https://github.com/andrediashexa/hexarouting/issues/285),
[#286](https://github.com/andrediashexa/hexarouting/issues/286),
[#287](https://github.com/andrediashexa/hexarouting/issues/287),
[#288](https://github.com/andrediashexa/hexarouting/issues/288).

### Added

* **The boot self-test reports the statistics segment, the one resource with
  an abort behind it**
  ([#288](https://github.com/andrediashexa/hexarouting/issues/288)).
  VPP exports its counters through a segment of fixed size. `hexarouting-tune`
  writes no `statseg` stanza, so a shipped box runs on VPP's built-in 32 MB,
  and each interface costs about 2.9 kB of it. Filling it is **not** a refused
  interface: `vnet_register_interface` reaches `os_panic`, the process takes
  `SIGABRT`, and the whole dataplane goes without a sentence in front of it.
  Measured while sizing a BNG: a box built by this product's own tuner aborted
  between 7,000 and 8,000 interfaces, twice, at the same step.

  ```
    [ ok ] statistics segment: 4.20M of 31.99M used, 3 interface(s)
          size: VPP's own default, which this product does not set
  ```

  Half the segment is a warning and three quarters is a failed check naming
  the signal and the stanza that raises it. Three quarters rather than nine
  tenths because the counter directory is one contiguous block that is copied
  to grow, so a growth has to hold two copies and the last part of the segment
  cannot be spent. The size is read from the **dataplane's own heap** and its
  provenance from `startup.conf`, so a box somebody has already sized is not
  reported as running on a default - and the shipped base file's *comment*
  about `statseg` does not count as somebody having sized it.

  **The tuner still does not size the segment, and now says so.** Every other
  figure it writes comes from ground truth or from a number the operator set:
  hugepages from `HEXA_SUBSCRIBERS` then from the RAM the machine has,
  `buffers-per-numa` from the worker count, `main-heap-size` from the pages the
  kernel actually reserved. Nothing it reads predicts an interface **count**,
  and a CGNAT subscriber is not an interface - 200,000 of them share a handful
  of ports, so sizing from `HEXA_SUBSCRIBERS` would reserve roughly 540 MB for
  interfaces that never appear on a box whose RAM floor is 4 GB. A constant
  would be a number nobody can justify. So the number stays the operator's, in
  `/etc/hexarouting/vpp-base.conf`, which the tuner concatenates ahead of what
  it generates:

  ```
  # statistics segment: NOT sized here, so VPP's built-in 32 MB applies.
  #          Measured: a box on that default aborted between 7,000 and 8,000
  #          interfaces. It is per INTERFACE, not per subscriber, which is why
  #          nothing here sizes it. To raise it, put a statseg stanza in the
  #          base file (/etc/hexarouting/vpp-base.conf):
  #          it is concatenated ahead of these stanzas.
  ```

  `appliance/test_tune.py` asserts the tuner generates **no** `statseg` stanza
  in either the tuned or the first-boot configuration, so an operator's own is
  never overridden on the next boot. `docs/appliance.md` carries what one
  interface costs, read from VPP's sources: 17 directory entries of 144 bytes
  plus 200 bytes per thread, which predicts the 2.9 kB measured at 40,000
  interfaces. It also records what could **not** be determined without running
  VPP and is claimed nowhere: the interface count a given segment size
  supports, because the contiguous directory's growth depends on whether the
  allocator can extend it in place.

* **`show cgnat ha`: whether two boxes would map subscribers the same way**
  ([#8](https://github.com/andrediashexa/hexarouting/issues/8)).
  In deterministic mode a subscriber's public address and port block are a
  function of the pool definition, so two boxes with different pools fail
  **silently**: the failover starts handing subscribers other blocks and the
  logs on both boxes look healthy. Nothing on the box could answer the
  question at all - the command did not exist, and on a box with FRR up it
  answered `% Unknown command`.

  ```
  warning: pool 'residencial' does not map subscribers the same way on the two
      boxes, so a failover moves them to other public addresses and port
      blocks, and neither box logs anything
      this box: block-size 2048
      the peer: block-size 1024
  ```

  **Which fields must match is derived, not listed.** The digest covers
  `Pool.lines(in_place=False)` - the same text `Config.delta_to` uses to
  decide a pool must be rebuilt, whose classification `test_hexash_config.py`
  asserts field by field and asserts is complete. A second list of "fields the
  peers must agree on" would be a second copy of that decision. Asserted in
  the same loop that owns the table: a mapping field whose change does not
  move the digest fails there, and so does an in-place field whose change
  does.

  **A warning at `commit`, never a refusal.** The two boxes cannot be
  committed in the same instant, so every legitimate capacity change diverges
  the pair between the first commit and the second. A refusal would fire on
  every correct procedure.

  **What it does NOT do**, said here because a release note that implies HA
  would be worse than none: nothing elects a role, moves a service address or
  takes anything over. The peer's configuration reaches this box as a file at
  `/etc/hexarouting/peer.conf` - what `save` writes, copied across - because
  there is no session between the two boxes until
  [#7](https://github.com/andrediashexa/hexarouting/issues/7). Every way that
  file can be missing or unreadable is reported as itself and never as
  agreement, and the report prints the file's date beside the digest: a match
  against a configuration the peer stopped running last week is the same false
  agreement the check exists to remove.

* **`commit` says when an eBGP session is opened with no `maximum-prefix`**
  ([#145](https://github.com/andrediashexa/hexarouting/issues/145)).
  A peer that leaks a full table into a box sized for a few thousand routes
  takes it down, and BGP stops nothing by default: FRR accepts every prefix
  the neighbour sends until memory runs out. `maximum-prefix` is the only
  statement that tears the session down instead, and nothing in this box ever
  mentioned that it was absent.

  ```
  warning: this eBGP session has no 'maximum-prefix': a peer that leaks a full
      table fills this box and nothing here stops it. 'neighbor 10.0.0.2
      maximum-prefix 10000' tears the session down instead of the box
      neighbor 10.0.0.2 remote-as 65001
  ```

  **A warning and never a refusal**, which is what the issue asking for it
  decided: a route server, a customer whose table is known small and a lab
  peer all run without a limit on purpose, and refusing them is the shape
  [#208](https://github.com/andrediashexa/hexarouting/issues/208) named.

  **It speaks once.** Only about the neighbours THIS CHANGE brings into that
  state, the same subtraction the undefined-route-map refusal makes. A box
  with three deliberately unlimited upstreams would otherwise carry three
  warnings on every commit it ever takes, and an alarm that is always on is
  one nobody reads.

  **iBGP is out of scope by decision, not omission**: the peer is a box the
  same operator runs, its table is the table this AS already decided to hold,
  and a limit there fires on the network growing rather than on a leak. So is
  a neighbour whose AS is not in the text - `remote-as auto`, or no
  `remote-as` at all, which `bgp_errors` already refuses for a reason that
  matters more. A peer group carrying the limit covers every member of it,
  because the group is where an operator with six upstreams puts it exactly
  once; `maximum-prefix-out` does not, because that statement limits what this
  box **sends**. The local AS is read per `router bgp` stanza, so the same
  neighbour AS is eBGP in one VRF and iBGP in another.

  Recorded because the mutation said so and not because anybody noticed it:
  the assertion that OSPF's `neighbor` is not a BGP session stays **green**
  with the `router bgp` guard taken out of the walk, because `poll-interval`
  is neither a `remote-as` nor a `peer-group` and the undecidable case is
  skipped anyway. The property is true; that guard is not what pins it.

### Fixed

* **Attaching CGNAT to an interface cost time proportional to how many
  interfaces the box already had**
  ([#287](https://github.com/andrediashexa/hexarouting/issues/287)).
  `set interface bpa in2out` cost about **4.3 ms** per call on a box holding
  5,000 interfaces and **8.3 ms** at 10,000, so attaching ten thousand took
  **83 seconds** of main-thread work. On a box with workers every one of those
  calls takes the worker barrier, and while it is held no packet moves.

  **The feature arc was not to blame**, and three controls measured in the same
  session say so: `set interface mtu` 32 µs, `policer add` 22 µs, and
  `policer input` - which is also a `vnet_feature_enable_disable` on the same
  `ip4-unicast` arc - 42 µs. The attach was two hundred times the cost of the
  arc operation it performs.

  The cause was ours. `bpa_interface_feature_enable_disable()` called
  `bpa_auto_mss_update()`, which recomputed what `tcp-mss auto` resolves to -
  the smallest IPv4 MTU among the interfaces the CGNAT runs on - by walking
  **every interface on the box** and asking the feature arc four times per
  interface whether the CGNAT was on it. Each attach re-walked the whole set,
  so N attaches were N walks.

  **The minimum is maintained now instead of recomputed.** Adding an interface
  can only lower a minimum, which is one compare against one MTU, and that is
  the operation the quadratic was made of. Taking one away is the direction a
  running value cannot answer, so the plugin also counts how many interfaces
  sit exactly at the minimum: every removal above it, and every removal from a
  minimum that more than one interface shares, is a decrement. Only the
  departure of the last interface holding the smallest MTU on the box looks at
  anything else, and what it looks at is a flat array of MTUs rather than VPP's
  interface pool through the feature arc.

  **Nothing an operator sees changes.** `tcp-mss auto` still resolves to the
  same number, `show bpa pool` still prints it, and a box with a handful of
  interfaces behaves exactly as before. What changes is that the cost of an
  attach no longer depends on the size of the box.

  **The cost is asserted by counting, not by timing.** A wall-clock assertion
  would be a fact about the machine rather than about the code, and this
  repository runs a release lab suite alongside `make test`, where a stopwatch
  goes red for reasons that have nothing to do with what it is measuring. So
  `test/test_bpa_auto_mss.c` attaches 8,192 interfaces and asserts that the
  last one reads exactly as many slots as the first, and that the whole sweep
  reads 8,192 slots rather than the 33 million a rescan-per-attach would need.
  It asserts the ANSWER against a full walk written independently in the test,
  after every one of twenty thousand changes in an arbitrary order, because
  incremental bookkeeping is wrong in ways that depend on the order operations
  arrive in. `test/test_attach_cost.py` asserts the half a unit test cannot
  see: that the attach path still calls the constant-cost update, since putting
  the walk back restores the quadratic with every unit assertion still green.

  **The limit, stated rather than left to be found.** A box whose interfaces
  all have DIFFERENT MTUs, detached narrowest first, still rescans on every
  detach. That rescan is a scan of a `u32` array rather than a walk of VPP's
  interface pool through the feature arc, and it is not on the attach path at
  all - but it is not constant, and the detach direction was not what was
  measured.

* **A virtio NIC could never be the dataplane interface, so every box in a VM
  was given no card** ([#286](https://github.com/andrediashexa/hexarouting/issues/286)).
  Reported from a box under evaluation: `show interface` still had nothing,
  with the interface named.

  `/sys/class/net/<if>/device` points at the device that OWNS the netdev, and
  on a virtio guest that is the virtio bus device, whose PARENT is the PCI one:

  ```
  /sys/class/net/ens18/device -> ../../../virtio3
  realpath: /sys/devices/pci0000:00/0000:00:12.0/virtio3
  ```

  The card has a PCI address - `0000:00:12.0`. Both readers took the basename,
  got `virtio3`, decided it was not PCI, and the tuner wrote `no-pci`. DPDK
  drives virtio through its own poll-mode driver, so the card was always
  eligible; the resolution stopped one directory short.

  **Bare metal is why it was not noticed.** An Intel NIC's `device` link points
  straight at the PCI device, and there the basename IS the address - so the
  same code works on the hardware it was written against and fails on every
  VM, which is where it is evaluated and where a great many deployments run.

  Both readers walk up to the nearest PCI ancestor now, and a check drives them
  against the same tree and compares their answers: the tuner decides what goes
  into `startup.conf` and `show hardware` decides what the operator is offered,
  so a box where those two disagree offers a card the tuner will refuse.

  The shape test gained sysfs's field widths. Without them `a:b:c.d` parses as
  a PCI address, and what this decides is which directory is handed to DPDK.

* **Six boot self-test lines ran past the 80-column console, the widest at 153
  columns** ([#285](https://github.com/andrediashexa/hexarouting/issues/285)).
  The self-test writes to `/dev/console` on a racked appliance. A line only
  goes over when a value is interpolated into it, so reading the source finds
  nothing; driving all 92 fixtures finds them.

  This is read by somebody who has just booted a box that is not working, at a
  serial console because SSH does not answer - so the terminal folds the line
  for them, and the interface name, the PCI address or the 31-cpu list arrives
  split across the fold, inside the message that was meant to say what is
  wrong. Five of the six were `[FAIL]` lines, and the widest was the
  worker-pinning failure, which is what an operator reads when the box is
  forwarding badly.

  Each value now has a row of its own, the way the VTY has done since
  [#252](https://github.com/andrediashexa/hexarouting/issues/252). The cpu list
  is FOLDED rather than given a row, because it is a list and not a token: `fold
  -s` breaks between numbers and never cuts one. A PCI address or an interface
  name is still printed whole, and may still run past the margin, because
  splitting what the reader greps for destroys it.

  Two of the six came from checks added the same week, so this was not old debt
  settling: it is the same mistake being made again in a file that had nothing
  measuring it. The check that #163 scoped to the tuning lines now covers every
  line - 248 distinct ones over 92 fixtures, widest 78 columns.

  Splitting the messages broke six of the suite's own needles, which is
  [#279](https://github.com/andrediashexa/hexarouting/issues/279) a fourth
  time and the worst instance of it: half of those needles are the ones
  checking that a FAILURE is reported. `said_across` is the matcher that asks
  whether the box said something rather than where it folded.

* **`HEXA_LOW_LATENCY=true` turned the latency tuning OFF, and
  `HEXA_SUBSCRIBERS=200k` failed the whole tuning unit — and nothing had ever
  run the script that does both**
  ([#163](https://github.com/andrediashexa/hexarouting/issues/163)).
  `appliance/apply-tuning` reads `/etc/hexarouting/tune.env` on every boot and
  turns it into the kernel command line. It was tested as **text**:
  `test/test_kernel_tuning.py` greps it for candidate knobs that must not be
  set, and `appliance/test_tune.py` tests the tuner it calls. Nothing executed
  it. Two defects were measured by running it for the first time:

  ```
  HEXA_LOW_LATENCY=true  ->  # ARGV: --write --subscribers 200000 --no-low-latency
                             HEXA_LOW_LATENCY=0: leaving the cpufreq governor alone
  ```

  Any value that was not exactly `1` took the "not 1" branch. A typo meaning
  **yes** stripped `intel_idle.max_cstate=0`, `processor.max_cstate=1` and
  `transparent_hugepage=never` off the kernel command line and left the
  governor alone — the opposite of what it says — while the journal reported a
  value the operator never wrote. And:

  ```
  HEXA_SUBSCRIBERS=200k  ->  hexarouting-tune: error: argument --subscribers:
                             invalid int value: '200k'
                             rc=1
  ```

  The unit failed, **no tuning was written at all**, and the kernel command
  line stayed at whatever the previous boot put there — disagreeing with the
  `startup.conf` beside it. That is the failure mode the `HEXA_MAX_WORKERS`
  guard in the same file already named as "far worse than an ignored setting",
  standing unfixed for the two values next to it.

  Every value is now checked before it is used, and one this box does not
  accept is **refused out loud and replaced by the shipped default** — never by
  the opposite of what it says, and never by failing the unit. The refusal says
  **what the box does accept**, with what each value would do, because it is
  read on a serial console by somebody who then has to guess the spelling:

  ```
  REFUSED HEXA_LOW_LATENCY='true' in /etc/hexarouting/tune.env.
    Two values are accepted, and nothing else:
      1  deep C-states off, transparent hugepages off, and the
         cpufreq governor held on performance
      0  leave all three alone, for a box that must save power
    Using 1, the shipped default, because this is a router.
    Fix the file and restart hexarouting-tune.service.
  ```

  `test/test_apply_tuning.sh` is new and drives the script against fake `/etc`,
  `/proc` and `/sys` trees through a `HEXA_ROOT` seam, with the tuner,
  `update-grub` and the NIC binder stubbed on `PATH` — the same shape
  `test/test_bind_nic.sh` uses. Thirty-two assertions, each shown failing: which
  arguments each value produces, that the governor is written only where the
  kernel offers `performance`, that the tuner runs before the NIC binder and a
  failing binder does not fail the tuning, that `update-grub` runs on a change
  and not on a repeat, and that a tuner which really fails **still** fails the
  unit, so the refusals did not turn every error into a shrug.

* **The boot self-test reported the C-state tuning it was ASKED for as though
  it were the tuning in effect, and told operators to do what they had already
  done** ([#163](https://github.com/andrediashexa/hexarouting/issues/163)).
  The line read `/proc/cmdline` and nothing else. `/proc/cmdline` is a request:
  it is what the bootloader asked for. So a box with `HEXA_LOW_LATENCY=0` —
  deep C-states left on, deliberately — was warned that deep C-states were
  still enabled and advised to *"set `HEXA_LOW_LATENCY=0`"*, about a state it
  had been told to be in. And the case worth the most had **no line at all**:
  `tune.env` saying one thing while this boot's command line says the other,
  which is a box running a command line older than the file that generates it.

  It now reads **two** sources and says which is which, and the four
  combinations are four different sentences. `tune.env` is read, never sourced:
  sourcing a file an operator edits would run its contents as root inside the
  boot self-test. A value the box does not accept is named on the console and
  read as `1`, which is what `apply-tuning` does with it — two components
  disagreeing about one file is worse than either being wrong. Beside it sits
  the one readback available without a reboot: where the kernel exposes no
  idle-state driver, nothing on the box enters a C-state and those parameters
  have nothing to act on, so the green line was a green line about nothing.

  **Not claimed**, and recorded in `docs/appliance.md` as not claimed: whether
  `intel_idle.max_cstate=0` was *taken* by a kernel that does have idle states.
  `/sys/module/intel_idle/parameters/max_cstate` reads `9` on the development
  machine with no such parameter on its command line, but confirming that it
  follows the boot parameter needs a reboot of a machine with a real idle
  driver, and neither is available here.

* **The candidate-knob sweep read three files while the other half of the same
  table read the whole tree, and a missing file killed the suite instead of
  failing it** ([#163](https://github.com/andrediashexa/hexarouting/issues/163)).
  `test/test_kernel_tuning.py` asserts that none of the boot-tuning candidates
  waiting on [#28](https://github.com/andrediashexa/hexarouting/issues/28) is
  applied. It looked for candidate **parameters** in the generated command
  line, `apply-tuning` and `sysctl.conf` — three files — while looking for
  candidate **sysfs paths** across all of `appliance/`. A parameter can reach a
  box from a systemd unit's `ExecStartPre`, from the installer, from
  `build-iso.sh`, or from a branch of `hexarouting-tune` that the one synthetic
  topology the suite renders never takes. It now sweeps every file the
  appliance ships, 28 of them, and fails when there are fewer than ten to sweep
  — an empty sweep finds nothing wrong with everything.

  Renaming `appliance/etc/sysctl.conf` produced a `FileNotFoundError` and **no
  verdict line at all**, so the runner reported the suite as unread rather than
  failed. That is the same repair the `section()` helper already carried, for
  the other half of the same file.

* **Every branch merged after a release filed its entry into a section that
  had already shipped** ([#284](https://github.com/andrediashexa/hexarouting/issues/284)).
  Five in one day after 0.68.0 was cut: #280, #281 and #39 into `## 0.68.0`,
  #145 into `## 0.69.0`, and #163 into `## 0.66.0`, which shipped in February.

  Nothing was wrong with any of the branches. Each was cut while master said
  `## Unreleased` and added its entry there; by the time it merged, the release
  had renamed that heading, so git had nothing to match and anchored on the
  `### Fixed` of whatever section was now at the top. No conflict, no warning.

  A release rename leaves an empty `## Unreleased` above the new section now,
  which gives git the anchor it was looking for. The release gate already
  tolerated that - its check refuses `## Unreleased` with entries under it, not
  an empty one - so nothing there had to change.

  `test/test_changelog_landing.py` holds it, in `make test` rather than in the
  gate: by the time the gate runs, the merges have already gone wherever they
  were going.

## 0.69.0 — the appliance brings up its own dataplane card, and a deterministic pool has one to give

[#39](https://github.com/andrediashexa/hexarouting/issues/39),
[#105](https://github.com/andrediashexa/hexarouting/issues/105),
[#123](https://github.com/andrediashexa/hexarouting/issues/123),
[#252](https://github.com/andrediashexa/hexarouting/issues/252),
[#271](https://github.com/andrediashexa/hexarouting/issues/271),
[#276](https://github.com/andrediashexa/hexarouting/issues/276),
[#279](https://github.com/andrediashexa/hexarouting/issues/279),
[#283](https://github.com/andrediashexa/hexarouting/issues/283).

### Breaking

* **The binary API moves to 0.13.0 and two messages change layout.**
  `bpa_pool_add_del` gains `block_reserve`, and `bpa_pool_details` gains
  `block_reserve`, `blocks_mapped` and `reserve_count`
  ([#39](https://github.com/andrediashexa/hexarouting/issues/39)). A client
  built against 0.12.0 must be rebuilt; the CRC will not match. `block_reserve`
  is deliberately **absent** from `bpa_pool_settings_set`, whose contract is
  "safe to change on a pool that is carrying traffic" — a reserve is not, and a
  field there would be accepted by a dataplane that cannot act on it.

* **A refusal naming a statement printed it and its reason on one row, up to
  264 columns wide, and `rollback`, `save` and `--load` had never been
  measured at all**
  ([#276](https://github.com/andrediashexa/hexarouting/issues/276)).
  [#252](https://github.com/andrediashexa/hexarouting/issues/252) and
  [#271](https://github.com/andrediashexa/hexarouting/issues/271) put
  everything `commit` and `commit check` print inside the 80 columns of an
  appliance console. Both scoped themselves to those two commands, and both
  left the same shape standing elsewhere: a row carrying a **quoted line and
  prose at once**, where neither half can be guaranteed - a statement MUST be
  printed whole because the operator greps for it and types it back, a
  sentence MUST be wrapped because it is read, and under one budget
  guaranteeing either breaks the other.

  #271 measured the eight BGP, IS-IS, OSPF, route-map, AS-path and
  prefix-range checks inside the budget on every fixture it drove and
  therefore did not touch them, recording that as a limit rather than a clean
  bill. Driven on the input an ISP actually types - an IPv6 neighbour, a
  route-map named for the policy it carries, an IPv6 prefix-list, a crypt hash
  pasted without its leading `$`, the box's own `/etc/hexarouting/` paths -
  **every one of them is over**, from 104 to 264 columns, and so is every row
  `rollback`, `save` and `--load` print that carries a path or a configuration
  line beside its reason. Twenty-three rows in all, from 80 to 264 columns.

  Two of them are 80 columns, one over, and both look like they fit: that is
  the whole argument for driving the commands instead of reading the source.

  ```
  error: no route-map called 'RM-IN' is defined, and FRR denies everything this
      statement governs until one is; define it, or write 'route-map RM-IN
      permit 10' to let everything through
      neighbor 10.0.0.2 route-map RM-IN in
  ```

  ```
  error: refusing to load this file, these lines are not understood:
      /etc/hexarouting/hexarouting.conf
      line 2: password 6$Xq7kPz2r$4Pv3cJ9sQm2WdT1hRfLb8XkY...
          (password: expected a crypt hash such as $y$... or $6$... (openssl
          passwd -6), not a plaintext password)
  ```

  The second is read at **boot**, on a box refusing its own saved
  configuration, which is the worst moment to be handed a broken row. A quoted
  line still runs past the margin when it has to - the hash above is 114
  columns on its own row - and that is the rule, not a gap in it.

  Six rows measured **inside** the budget on the same run and were left
  exactly as they were, which is the discipline #271 applied to the checks: a
  message that does not measure over is a message this change has no business
  rewording.

  **Breaking, for anything scripted against the text.** Fourteen refusal and
  warning rows changed shape, and nine headlines changed wording:
  `<statement>: <reason>` is now the reason, wrapped, with the statement on
  the row below; `<path>: refusing to load, ...` is now `refusing to load this
  file, ...` with the path on the row below; and the same for `rolled back,
  but <path> could not be removed`, `giving up after N attempts`, `loaded, but
  no rollback revision could be recorded`, `revision N cannot be read`,
  `cannot write <path>` and `the configuration to roll back to has lines this
  VTY does not understand`, which is now `... cannot read`. Nothing the box
  *does* changed.

  A wrapped sentence also breaks a needle that straddles the break, and one
  lab assertion did. `lab/verdict.sh` gained `expect_said`, which is
  `expect_in` over the haystack with whitespace collapsed - the shell half of
  `unwrapped()` that #271 added to `test/test_hexash_bgp.py` for exactly this.
  A needle that must stay on one row keeps `expect_in`.

  The twenty-two new fixtures append to the same `_refusal_out` list #252's
  sweep already reads, each carrying a needle proving it reached its branch,
  with `apply()` not stubbed - one list, one budget, one check.

### Added

* **`reserve`: blocks a deterministic pool can give away**
  ([#39](https://github.com/andrediashexa/hexarouting/issues/39)). A
  deterministic pool had no spare block by construction - the formula assigns
  every block to some subscriber - so a customer whose block filled up was
  dropped and their internet stopped while the box was healthy.
  `reserve <n>` holds n blocks of **every public address** out of the formula,
  and those are the blocks a subscriber that fills its own is given.

  **It is paid for in capacity, and it re-maps subscribers.** `capacity` is now
  `outside.count × (blocks_per_ip - reserve)`, so enabling or resizing a
  reserve is a capacity change and `commit` refuses it until it is asked for by
  name with `commit remap`, exactly like adding a public prefix
  ([#23](https://github.com/andrediashexa/hexarouting/issues/23)). How much it
  moves depends on the policy and the two answers are not close, measured on a
  pair of definitions that differ only in the reserve: under `interleaved` the
  divisor is untouched, so **no retained subscriber moves** and the pool is
  simply truncated by `outside.count × reserve` subscribers; under `blocked`
  the divisor **is** the mapped block count, and 738 of 882 subscribers move on
  top of the 21 the truncation drops.

  **The default is zero and zero is byte for byte what the box did before**,
  asserted rather than promised: `ref/validate.py` compares every mapping of
  every pool against the same pool built without the parameter, and
  `test/test_bpa_pool.c` asserts that five pools with no reserve hold nothing
  back and reserve no port - including the last ports of the range, which are
  the ones a reserve takes first. `lab/detreserve.sh off` runs the whole lab
  against `reserve 0` and the subscriber stays on one block.

  **Every reserve block is its own RFC 8158 `natEvent 16`, and its release its
  own `natEvent 17`.** This is the only thing deterministic mode logs per
  subscriber, and it has to be: the reserve block is the one block in that mode
  the formula does not produce, so the dated configuration trail cannot answer
  for it. `lab/detreserve.sh` reads both records off a real IPFIX collector on
  the far side of a wire, at the port range the model predicted.

  **The return path resolves a reserve port through the allocator.**
  `bpa_reverse_sub()` refuses one on purpose - under the blocked policy its
  arithmetic would name a real and different customer, who would then receive
  somebody else's traffic - so the reserve has an index space of its own, and
  the handoff steers on it.

  **Running out of reserve is counted apart from reaching `max-blocks`**, in
  `show bpa blocks`, because the two need different actions: a larger reserve
  against one and a larger cap against the other.

  **The control the case rests on was not being performed.** `lab/detreserve.sh`
  says in its header that running with `reserve 0` must turn the extension
  assertions red, and that "that argument is the only thing that makes the
  green run worth anything". The lab was not in `OFF_MODES`, so the suite never
  ran it - and the off run would not have been a control anyway: the extension
  assertions sat inside `if [ "$RESERVE" -gt 0 ]`, with three different
  assertions in the else branch, so it passed with nothing red. Since
  `tools/run-all-labs.sh` INVERTS an off run's verdict, that pass would have
  been counted as a failure of the suite.

  The extension assertions run in both modes now. With `reserve 0` nine of them
  go red - measured, and written into `# OFF-RED-AT-LEAST: 9` - and the three
  that say what the box does instead stay green. A tenth check stays green in
  both modes and is deliberately not counted: "the second subscriber found the
  reserve empty" is true for free when there is no reserve, and an assertion
  that cannot discriminate is not evidence.

  Three limits, stated rather than left to be discovered. A reserve smaller
  than the worker count leaves some workers with none, and that is a **warning
  and not a refusal**, for the reason `block-stride` gives: refusing would stop
  a pool accepted on a single-threaded box from coming up after the box is
  given workers. The subscriber's own public address is a **preference** for
  where its reserve block comes from, not a guarantee. And a reserve on a
  **dynamic** pool is refused outright - that pool's extension already comes
  off the free list, so a reserve would take blocks out of the list it is drawn
  from.

* **`show hardware` - the VTY lists the box's own NICs, so the operator can
  see what there is to choose from**
  ([#283](https://github.com/andrediashexa/hexarouting/issues/283)).
  On a fresh appliance VPP has been given no NIC, so `show interface` is empty
  - and that is exactly when somebody needs to see the box's hardware in order
  to name one. The only screen that could answer was the one with nothing to
  say, and the way to the list was to leave the VTY and read `/sys` or run `ip
  link`. For a product whose argument is that it is driven from `hexash`, a
  first setup step that is a shell is a VTY that is decoration.

  Per NIC, read from `/sys/class/net`: name, PCI address, the driver holding it
  **now**, whether that driver is one DPDK can use (`vfio-pci`,
  `uio_pci_generic`, `igb_uio`) or a kernel driver, link state, MAC and speed.

  ```
  hexarouting> show hardware
    the dataplane has no interface to forward on: no interface was named, so VPP
    was given no NIC.
    Set HEXA_DATAPLANE_IF in /etc/hexarouting/tune.env to the interface carrying
    subscriber traffic,
    then restart hexarouting-tune and vpp.

    Interface     PCI            Driver           Link   Speed
    eno1          0000:03:00.0   ixgbe            up     1000Mb/s
      3c:ec:ef:1a:2b:3c  kernel driver: DPDK cannot take a NIC through it
      *** carries the DEFAULT ROUTE ***
    ens1f0        0000:5e:00.0   ixgbe            down   -
      b4:96:91:0d:1e:2f  kernel driver: DPDK cannot take a NIC through it

    2 NIC(s) on this box, as Linux has them
    'eno1' carries the default route: it is how this box is reached, and giving
    it to the dataplane would take the box off the network. It is refused as a
    dataplane interface for that reason.
  ```

  **The default-route mark is the point of the screen as much as the list is.**
  Handing that NIC to the dataplane does not degrade the box, it removes it, and
  [#123](https://github.com/andrediashexa/hexarouting/issues/123) made the tuner
  refuse it for that reason - *after* the operator has chosen, which is too late
  to teach anything. It is read from `/proc/net/route`, destination `00000000`,
  the same file and field the tuner reads, so the two cannot disagree. The PCI
  test is the tuner's too: a basename that is not shaped `0000:3b:00.0` is
  skipped, so a NIC this screen offers is one the tuner can resolve.

  **What the dataplane holds is said once, and before the list**, because a NIC
  VPP owns is not under `/sys/class/net` at all and a list that quietly omits it
  reads as a missing NIC - a hardware fault, and a different afternoon. When the
  dataplane holds nothing, that sentence is
  `Shell.why_no_dataplane_interface()`, the same reading in the same words as
  `show interface` and the boot self-test, rather than a second diagnosis of one
  fact.

  A box where Linux has no PCI NIC gets a sentence, not an empty table:
  *"nothing to show"* and *"nothing is wrong"* look identical on a screen, and
  this one is read on a box that forwards nothing. That check going blind is one
  of the twenty-six assertions, and was shown failing by making the command
  print its table header for a box with no NICs.

  Every path is an overridable seam - `Shell.SYS_CLASS_NET` and
  `Shell.PROC_NET_ROUTE`, beside the four that were already there - and the
  assertions run against a `/sys` tree built in a tempdir, driven through
  `sh.feed("show hardware")`, with nothing stubbed. Its `/proc/net/route` is
  ordered so a reading that took the first row naming an interface names the
  **wrong** NIC rather than passing by luck. All twenty-six were shown failing,
  each against a mutation of the product.

  Registered in the grammar, so `?` and TAB offer it. It is **not** `show
  hardware-interfaces`, which is the dataplane's own command for its ports; this
  one answers about the box.

* **The dataplane interface can be named from the VTY, so a fresh appliance
  can be put to work without leaving it**
  ([#283](https://github.com/andrediashexa/hexarouting/issues/283)). The empty
  `HEXA_DATAPLANE_IF` is the right answer to
  [#123](https://github.com/andrediashexa/hexarouting/issues/123) - with
  nothing named VPP probes and takes whatever NIC it finds, which on a
  single-NIC box is the management port - and it left the other half missing.
  **There was no way to name the card that was not an editor in Linux.** Every
  fresh install came up forwarding nothing, and the remedy the box printed was
  a path and a variable. `docs/context.md` argues that an operator who has to
  drop to `vppctl` is one for whom this VTY is decoration; the same holds for
  one who has to drop to `vi`.

  `dataplane interface <name>` is now a configuration statement like any
  other: it goes into the candidate, is committed, is written by `save`, and
  is rendered by `show running-config` and `show configuration`. The name is
  the one **Linux** gives the card, because before the handover the card
  exists only to Linux - and because `show interface`, which answers from the
  dataplane, is empty precisely when this question is being asked. `?` and TAB
  list the cards the box has and mark the one carrying the default route.

  **`commit` does not restart VPP, and that is the design rather than an
  omission.** It writes `HEXA_DATAPLANE_IF` into the tuner's file and restarts
  `hexarouting-tune`, which regenerates `startup.conf` and binds the NIC; then
  it says, inside the console budget, that the card has not moved yet, that
  moving it ends every session on the box including the operator's own, and
  that a reboot in a chosen window is what puts it in effect. That is the same
  class of consequence as `commit remap`, which this VTY refuses until it is
  asked for by name - and no verb has been invented for this one, because
  nothing in this repository has measured what a VPP restart costs on a box
  carrying traffic. A dangerous verb offered on no measurement is one somebody
  uses because they were told it was safe.

  **Every other line of `tune.env` survives byte for byte.** The file is
  hand-editable by design and most of it is prose - the paragraph on why the
  field ships empty, the worker cap and the argument for leaving it uncapped -
  so the assignment is replaced in place, through a temporary file and a
  rename, rather than the file being regenerated from the settings this VTY
  models. The suite asserts the other lines are unchanged in order and that
  the comment count is the same, because what a regenerated file loses first
  is the prose, and a check that only looked at the settings would pass over a
  file stripped of every comment.

  **Two refusals fire at the prompt, before anything is stored.** The card
  carrying the default route is refused, with what it would cost; a name the
  box has not got is refused with the list of the names it does have, which is
  the shape `interface <name>` refusals already use
  ([#105](https://github.com/andrediashexa/hexarouting/issues/105)). Both are
  checked again in `apply(check_only=True)`, because `rollback` and `--load`
  reach the same setting without passing the prompt. The first refusal already
  existed in `hexarouting-tune` and in the NIC binding, and is said here as
  well because *where* it is said is the difference: a refusal in a journal at
  the next boot is one the operator reads hours later, and this mistake is the
  one that costs a site visit. The default route is read from
  `/proc/net/route` - the same source the tuner and the bind script read, so
  the three cannot come to disagree about which card the box is reached over.

  The running configuration is **read back** from the tuner's file rather than
  remembered, like every other field: a box set up the old way shows its own
  card in `show running-config` without anybody retyping it, and a second
  commit of the same value is `no changes to commit`.

  The advice the box gives when it has no card was the other half of the
  defect and is changed with it. `Shell.why_no_dataplane_interface` and
  `appliance/selftest` both said to set `HEXA_DATAPLANE_IF` in a file; both
  now name the statement, in the same words, and the suites assert that
  neither sends the operator back to an editor.

  `test/test_dataplane_if.py` drives all of it against a fake `/sys/class/net`,
  `/proc/net/route` and `tune.env` in a tempdir, through the seams the product
  already has, with nothing about the path under test stubbed. Twenty-five
  mutations of the product were each shown turning it red, including one that
  restarts VPP from the commit and one that lets the default-route refusal
  through.

### Fixed

* **A third lab needle split by a re-wrapped message, and this one is in the
  lab the release gate runs**
  ([#279](https://github.com/andrediashexa/hexarouting/issues/279)).
  `lab/install-iso.sh` waits for the box to say, on a console nobody is logged
  into, that it has put the previous configuration back. Reflowing that
  sentence to fit the console moved the fold into the needle - but only at the
  minute count the lab uses:

  ```
  commit confirmed 10: nobody confirmed it within 10 minute(s), so this box has
  put the previous configuration back        <- needle intact

  commit confirmed 1: nobody confirmed it within 1 minute(s), so this box has put
  the previous configuration back            <- needle split
  ```

  The lab sends `commit confirmed 1`, so the assertion went red over a box
  doing exactly the right thing: every other check around it, including that
  the account really was reverted and that the timer really is what enforced
  it, stayed green.

  `wait_said` is `wait_for` over a console with its line breaks collapsed. The
  offset bookkeeping stays on the raw capture, because that is a byte count.
  The three other multi-word needles in that lab were checked and left on
  `wait_for`: all three come from `err()` or a bare `print()`, which emit one
  row with no wrapping, and none is over 55 columns.

  This lab is in `NOT_LABS`, so only the release gate runs it. That is why the
  first two of these were found in an hour and this one took a release.

* **The appliance binds its own dataplane NIC**
  ([#283](https://github.com/andrediashexa/hexarouting/issues/283)). Nothing in
  this product ever bound a NIC or loaded `vfio-pci`. DPDK cannot take a device
  through `ixgbe`, `i40e`, `bnxt_en` and the rest, so an operator who did
  exactly what the self-test told them - name the interface in `tune.env` - got
  a `startup.conf` with the right PCI address, a NIC still held by its kernel
  driver, and a dataplane with no interfaces. Mellanox worked, because `mlx5`
  is bifurcated and runs while kernel-bound, which is why this looked like it
  worked on some hardware and not others.

  `appliance/bind-dataplane-nic` runs from `apply-tuning`, after the tuner has
  chosen the device and before VPP starts. **It binds what `startup.conf`
  names**, which is the point: the tuner has already refused the interface
  carrying the default route ([#123](https://github.com/andrediashexa/hexarouting/issues/123)),
  so reading its output inherits that refusal instead of reimplementing it. A
  second copy of "which NIC may the dataplane have" is a second copy that can
  disagree, and this one would disagree by taking the box off the network. It
  checks the default route again itself before unbinding - not as a second
  opinion but as a last one, because `startup.conf` is a file a person can
  edit and the cost of being wrong is a box somebody has to walk to.

  `driver_override` and not `new_id`: `new_id` matches by vendor and device, so
  it claims every card of that model in the box, including the management port
  of a two-port card, which is the ordinary case.

  `vfio-pci` where there is an IOMMU and `uio_pci_generic` where there is not,
  with the fallback and what it gives up said out loud - `vfio-pci` on a box
  with no IOMMU loads and then refuses every device, which reads on the console
  as the bind failing for no reason.

  It verifies rather than assumes, and a bind that did not take says which
  driver the device is on now. A device left on none at all is the worst
  outcome to be quiet about: the kernel no longer has the NIC and neither does
  the dataplane. Its failure does not fail the tuning, because a box that
  cannot bind its NIC still wants its hugepages and still wants to come up far
  enough for somebody to log in and read why.

  Four reds, and the first is the one that matters: with the default-route
  refusal removed the box binds its own management interface.

* **A box whose dataplane had no interface passed its own self-test, and the
  VTY guessed at why** ([#283](https://github.com/andrediashexa/hexarouting/issues/283)).
  Reported from a real box: VPP still has no interfaces, and the only way to
  give it one is to edit a file in Linux.

  Two things were wrong before the missing feature is even reached.

  `appliance/selftest` reported `[ ok ] dataplane NIC: <address>` as soon as a
  device appeared in `startup.conf`. A name in a file is not a NIC: a box whose
  dataplane holds nothing but `local0` passed the one screen meant to find
  that. It now asks the dataplane, and a named device VPP does not have is a
  **FAILED** check.

  `show interface` ended its explanation with "the usual cause is the NIC still
  bound to a kernel driver" - a guess printed as a finding, when Linux will
  simply say. It now reads the driver actually holding the device and whether a
  driver DPDK can use is loaded at all, and tells the three states apart,
  because they need different work and read identically otherwise: bound to a
  kernel driver with nothing loaded, bound to a kernel driver with `vfio-pci`
  loaded and nothing bound to it, and already bound for DPDK - where the answer
  is that the binding is *not* the problem, so an operator who has done that
  work is not sent to do it again.

  **Nothing in `appliance/` binds a NIC or loads `vfio-pci`**, so an operator
  who does exactly what the box tells them - name the interface - lands in the
  second state and is still forwarding nothing. Naming it from the VTY, and the
  binding itself, are what #283 stays open for.

* **A check that a message is ABSENT stopped being able to fail**
  ([#279](https://github.com/andrediashexa/hexarouting/issues/279)).
  Reflowing the OSPF findings above split the sentence `it is facing
  subscribers, and it is not passive` across a line, and `lab/igp.sh` asserts
  it three ways: twice that the box says it, once that it does not. The two
  positive ones went red in the release suite and were fixed with
  `expect_said`. The negative one **passed**, because `expect_not_in` could no
  longer find the sentence whether the box said it or not.

  That is the worse half by a distance: a red check names itself, and this one
  would have gone on passing over a box that had started warning about the
  wrong interface. `expect_not_said` is the negative form, and the difference
  is measurable on the same input - given output where the box DID say it,
  wrapped, `expect_not_in` reports it absent and `expect_not_said` reports it
  said. On a box that is genuinely silent the two agree.

* **`docs/` claimed behaviour the box does not have, in sixty-odd places.**
  `README.md` makes `docs/` the reference every other document is corrected
  against, so a wrong sentence here becomes a wrong wiki page and then a wrong
  answer to an operator on the phone. A wiki sweep had been done; nothing had
  swept `docs/`. Every claim below was established from the product - by
  driving `hexash` in-process the way `test/test_hexash_*.py` do, by computing
  mappings with `ref/bpa_model.py`, or by reading the code that prints the
  thing - and the counts were taken from what they count.

  The shapes, because they repeat. **Quoted output that the box does not
  print**: the `commit remap` refusal in `pool-resize.md` and
  `architecture.md` was missing the live-state count, the second example and
  the closing paragraph, and `architecture.md` had hand-wrapped a line the VTY
  deliberately prints whole; the UDP-export refusal in `logging.md` was
  missing its last paragraph; `routing.md`'s `show cgnat ?` listed four of
  eleven sub-commands, `ip ?` offered a `route` keyword removed two releases
  ago, an interface block listed six of fifteen options, `show route` and
  `show interface utilization` were in orders `sorted()` cannot produce,
  `show interface <name>` was two columns under-indented, and one block showed
  `hexarouting(config-cgnat-pool)#`, a prompt this VTY has never printed;
  `appliance.md` quoted the medium's banner at version 0.5.0 and a
  confirmation that types the disk path back when the installer asks for the
  word `yes`. **Features documented as missing that shipped**: `roadmap.md`
  said BGP policy was "not in the grammar at all" (v0.48.0, v0.51.0-v0.55.0),
  an interface understood "`mtu` and `urpf` and nothing else" (v0.40.0-v0.44.0),
  there was no documented pool-resize procedure (v0.19.0), nothing detected
  tampering in the trail (v0.23.0), nothing warned below the RFC floors
  (v0.16.0), the suite never ran an `off` mode, and the read-back check was
  not written. **Counts that had drifted**: `context.md`'s "12,000-line
  dataplane, 4,400-line VTY and 35 labs" against 18,903, 13,939 and 50, and
  its "30,000+ vectors" against 13,670 generated ones; `routing.md`'s "four
  statements of twenty-five" against three of twenty-four, and "twenty-eight"
  console lines against thirty. Where a number had no stable source it was
  replaced by the property rather than by a fresher number, for the reason
  `README.md` gives.

  Two statements were the opposite of the product, and both change what an
  operator does: `logging.md` said the exporter uses a kernel socket, when the
  **UDP** export is a packet the dataplane sends - so a UDP collector must be
  reachable through the VPP FIB, not through Linux; and `context.md` said the
  VTY reaches the dataplane through vat2 and "not `vppctl` text", when
  thirty-eight read sites and the pool validation behind `commit check` are
  still `vppctl`. `routing.md` also told the operator the refusal names a
  statement in FRR's spelling, when `as_hierarchy()` exists precisely to name
  it in this VTY's.

  Three things are reported and **not** repaired, because the fix is a
  decision rather than an edit: `bpa_log.c` schedules the boot marker before
  replaying pins but emits it after them in the same drain, so `logging.md`
  and `bpa_log.h` both describe an order the wire does not carry;
  `show interface <name>` drops the first counter VPP prints, because VPP puts
  it on the interface's own line and the reader skips that row; and
  `roadmap.md` now has two `Phase 4` and two `Phase 5` headings, which cannot
  be renumbered without breaking the `phase 6` that `ha.md` points at.

## 0.68.0 — what the box said it did, and a release gate that could not be passed

[#9](https://github.com/andrediashexa/hexarouting/issues/9),
[#226](https://github.com/andrediashexa/hexarouting/issues/226),
[#227](https://github.com/andrediashexa/hexarouting/issues/227),
[#249](https://github.com/andrediashexa/hexarouting/issues/249),
[#250](https://github.com/andrediashexa/hexarouting/issues/250),
[#251](https://github.com/andrediashexa/hexarouting/issues/251),
[#252](https://github.com/andrediashexa/hexarouting/issues/252),
[#253](https://github.com/andrediashexa/hexarouting/issues/253),
[#254](https://github.com/andrediashexa/hexarouting/issues/254),
[#255](https://github.com/andrediashexa/hexarouting/issues/255),
[#256](https://github.com/andrediashexa/hexarouting/issues/256),
[#257](https://github.com/andrediashexa/hexarouting/issues/257),
[#258](https://github.com/andrediashexa/hexarouting/issues/258),
[#259](https://github.com/andrediashexa/hexarouting/issues/259),
[#260](https://github.com/andrediashexa/hexarouting/issues/260),
[#261](https://github.com/andrediashexa/hexarouting/issues/261),
[#262](https://github.com/andrediashexa/hexarouting/issues/262),
[#263](https://github.com/andrediashexa/hexarouting/issues/263),
[#265](https://github.com/andrediashexa/hexarouting/issues/265),
[#266](https://github.com/andrediashexa/hexarouting/issues/266),
[#267](https://github.com/andrediashexa/hexarouting/issues/267),
[#268](https://github.com/andrediashexa/hexarouting/issues/268),
[#269](https://github.com/andrediashexa/hexarouting/issues/269),
[#270](https://github.com/andrediashexa/hexarouting/issues/270),
[#271](https://github.com/andrediashexa/hexarouting/issues/271),
[#272](https://github.com/andrediashexa/hexarouting/issues/272),
[#273](https://github.com/andrediashexa/hexarouting/issues/273),
[#274](https://github.com/andrediashexa/hexarouting/issues/274),
[#275](https://github.com/andrediashexa/hexarouting/issues/275),
[#278](https://github.com/andrediashexa/hexarouting/issues/278),
[#279](https://github.com/andrediashexa/hexarouting/issues/279),
[#280](https://github.com/andrediashexa/hexarouting/issues/280),
[#281](https://github.com/andrediashexa/hexarouting/issues/281).

### Fixed

* **`show interface <name>` silently dropped `rx packets`, the first counter
  the dataplane prints** ([#280](https://github.com/andrediashexa/hexarouting/issues/280)).
  The code removed the column header with `[1:]` and then removed another row
  with a second `[1:]` - and that row is the interface's own line, which is
  where VPP puts the first counter.

  It did not look like a gap. The list began at `rx bytes` and ran to `drops`
  with nothing on the screen saying a row had gone, so a reader concluded the
  box has no `rx packets` or that it is zero. "Is anything arriving on this
  port" is the question this screen gets opened for, and it also broke the
  promise made two lines above the code: an operator comparing this against
  the dataplane during a fault got a different set of rows, which is what
  "straight from the dataplane, uninterpreted" exists to prevent.

  The identity in front of that first counter - name, index, state, MTU - is
  stripped rather than printed, because all four are already on the lines
  above and the row would not line up with the others. An interface with
  nothing after its MTU has no counters and says `all zero`, rather than
  presenting its own identity as though it were a reading. Both are asserted,
  and both go red on their own mutation.

  Found while checking `docs/` against the product.

* **A re-wrapped message silently broke two lab needles**
  ([#279](https://github.com/andrediashexa/hexarouting/issues/279)).
  `lab/routing.sh` went red in the release suite on `systemctl restart frr` and
  `FRR validates them at commit`. Neither message changed a word: #271 folded
  them to fit an 80-column console and the fold landed inside both needles -
  `'systemctl` ends one row and `restart frr'` starts the next.

  `expect_in` searches the raw text, so a needle only matches while the message
  it is in happens to fold somewhere else. All 40 unit suites were green,
  because none of them reads a lab; the only thing that could say so was a lab
  run, an hour into a release suite.

  `expect_said` in `lab/verdict.sh` collapses whitespace on both sides, so a
  sentence matches wherever it folds. It is the shell half of `unwrapped()` in
  `test/test_hexash_bgp.py`. `expect_in` stays for anything printed WHOLE - a
  statement, a path, a configuration line, a counter - because those must not
  wrap and collapsing first would hide it if they did.

* **A lab held a runner for 29 minutes without printing a line or failing.**
  `lab/oom.sh off` sat in `apt-get update` while the release suite waited on
  it. Measured inside the container: `getent hosts deb.debian.org` returns only
  IPv6 addresses, `curl -4` gets 200 and `curl -6` gets nothing, so apt chose a
  family this box cannot route and blocked. `tools/lab.sh` now forces IPv4 and
  sets a timeout, and the same lab finishes in 107 seconds.

  The timeout matters as much as the address family. A lab that fails says so
  in its verdict and the suite classifies it; a lab that hangs takes a slot out
  of the suite for as long as nobody is looking.

* **`commit check` promised to report which subscribers a change re-maps, and
  reported nothing** ([#278](https://github.com/andrediashexa/hexarouting/issues/278)).
  The VTY's own `?` help says so and six pages of the operator wiki repeated
  it. Staging an `outside` change over a running pool and running `commit
  check` printed `configuration check succeeds` and nothing else.

  The numbers were never missing: `self.remap` is filled by the same
  `apply(check_only=True)` that `check` runs, and was only printed inside the
  plain `commit` refusal, which `check` returns before reaching.

  It is the worst shape for this fact. An operator runs `check` in a
  maintenance window to learn what the commit will do before doing it, and a
  re-mapping is the most consequential thing a commit here can do: one added
  prefix moves every subscriber and ends every session on the pool, with
  nothing unusual reported afterwards because nothing unusual happened
  ([#23](https://github.com/andrediashexa/hexarouting/issues/23)). `commit`
  refusing later is the backstop, not the answer - by then the operator has
  already decided to go ahead.

  ```
  configuration check succeeds
    this change RE-MAPS subscribers that already have mappings
      194 of 256 subscribers move
      100.64.0.1 -> 200.150.0.9 port 1536-2047
      100.64.0.2 -> 200.150.0.10 port 1024-1535
    'commit' refuses this until it is asked for by name, with 'commit remap'
  ```

  Not printed as a refusal, because `check` applied nothing and an operator who
  reads it as a failure runs it again. And not printed on every check: a change
  that moves nobody says nothing, for the reason
  [#205](https://github.com/andrediashexa/hexarouting/issues/205) and
  [#208](https://github.com/andrediashexa/hexarouting/issues/208) give - a
  warning that is always on is one nobody reads. Both are asserted.

  The code moved and not the help. Making the help true by deleting half of it
  would leave the operator no way to learn this before applying.

  Measured on a box, not only in a fixture. `lab/commit.sh` already drives a
  real re-mapping against `ref/bpa_model.py` as an oracle, so `commit check`
  runs there on the same candidate and its count is compared with the one
  `commit`'s refusal gives: two answers to "how many move" would be worse than
  one. With the report removed the lab goes red on four checks, naming the real
  number - `240 of 256 subscribers`. The lab also asserts what a fixture
  cannot, that `check` left the block size where it was.

* **Six wiki sentences promised that `commit check` reports re-mappings, and
  it reports nothing.** Staged an `outside` change over a running pool and fed
  it to `commit check`: the output is `configuration check succeeds` and
  nothing else. `self.remap` is populated, and is only printed inside the plain
  `commit` refusal, which `commit check` returns before reaching. The pages say
  where the numbers actually come from now, and quote that refusal as it
  prints. **The VTY's own `?` help for `commit check` makes the same promise**,
  which is a defect in the product rather than in the pages and is not fixed
  here.

* **The wiki was checked page by page against the box, not read.** Every
  quoted block that was wrong is regenerated by running the code that prints
  it. `commit cutoff` was absent from the wiki entirely; the automatic
  `commit confirmed` message was the text from before that check existed; a
  pool has five ALGs and one page listed three; `linux-name` and loopbacks
  appeared nowhere, and without `linux-name` "BGP, OSPF and IS-IS were
  configurable and inert"; an account's `role` is configuration and two pages
  said it was not, so the recipe for adding a CGNAT operator produced a
  read-only account; pipe filters do strip typed quotes and two pages said
  they do not.

  Thirty-two pages read. What was checked and found correct is recorded on the
  branch, because a sweep that reports only its hits is one nobody can judge
  the coverage of.

* **The selftest kept its own list of routing daemons, and nothing compared it
  with the grammar** ([#275](https://github.com/andrediashexa/hexarouting/issues/275)).
  `appliance/selftest` decides which daemons a box should be running from
  `for d in bgpd ospfd ospf6d isisd`; the VTY decides the same thing from
  `TREE["config"]["router"]`. Two copies of one truth, in two languages.

  That list has already been wrong this way. #187: the VTY accepted `router
  ospf6`, the installer enabled every daemon but `ospf6d`, and OSPFv3 became
  the one protocol a box could be configured for and could never run. The
  comment above the loop says so. A comment is the repair for the time it
  happened, not a check against the next one - and the next one is a fresh box
  passing its own self-test while unable to run what somebody is about to
  configure on it.

  `test/test_selftest_daemons.py` compares the two, both ways. The other
  direction is a warning about a daemon nobody on that box has a use for,
  which is how a self-test becomes something operators scroll past.

  The comparison is in `make test` rather than in the shell: the selftest runs
  on a box, early, and must not depend on the VTY being importable - the same
  reason `test/test_install_manifest.py` reads `appliance/install.sh` from
  here.

  Four reds: `ospf6d` dropped from the loop, which is #187 exactly; a protocol
  added to the grammar and not to the loop; a daemon in the loop the grammar
  cannot commit to; and the loop renamed, so the check reads nothing at all.
* **The public wiki told operators that a refused commit applies nothing, and
  one refusal leaves the rest applied**
  ([#254](https://github.com/andrediashexa/hexarouting/issues/254),
  [#227](https://github.com/andrediashexa/hexarouting/issues/227)).
  `commit-rollback-e-save.md` said it twice - once in a `:::warning` box and
  once at the head of the refusal list - and it was true of every refusal this
  VTY had until the read-back check was promoted from a warning to a refusal.
  That refusal cannot be decided before applying: it IS the discovery that FRR
  took the configuration and did not keep it. Its own message says so, in as
  many words: "nothing was rolled back: the rest of it is applied".

  An operator who has just been refused reads that page to find out what state
  their box is in, and it told them the box was untouched. It is not: the CGNAT
  half and the box's own configuration are in effect, and `rollback 1` is the
  way back. Somebody acting on it walks away from a half-configured box
  believing it is unchanged.

  Both places now separate the refusals decided before anything is applied from
  the one decided after, and the read-back refusal has a section of its own -
  it had none, which is why the page could describe every refusal as the same
  kind. The message in it is what the box prints, produced through
  `print_prose` rather than wrapped by hand, because a page that quotes output
  the box does not produce is the same defect again.

  `wiki/` is a mirror; publishing needs `WIKI_TOKEN`, which is not available
  here. The repository is correct now and the push is somebody else's.

* **The release gate refused every future release, for duplicates in sections
  it also forbids editing**
  ([#274](https://github.com/andrediashexa/hexarouting/issues/274)). The
  duplicate-heading check added for #267 read the whole file. The whole file is
  where the six duplicates already are - three in 0.67.0, two in 0.66.0, one in
  0.14.0 - and every one is in a section that has shipped, which the check
  above it refuses to let anyone edit. Measured on a dry run of 0.68.0: the
  gate refuses, naming six duplicates across three releases, none of which
  anyone is allowed to touch.

  It reads the section being released now. The tagged sections are not going
  unchecked; they are held by the stronger rule, which is not "has no duplicate
  headings" but "is byte-for-byte what its tag shipped", duplicates and all. A
  duplicate that shipped is part of a medium somebody is running.

  A gate nobody can pass is a gate somebody switches off, and this is the only
  thing in the project that has ever caught a release mistake.

  An empty extraction is a refusal of its own now. A check that stops reading
  passes silently, which is worse than the defect it looks for; shown by
  blinding the section match, and the guard fires.

* **The same entry appeared twice, and one release section had two `### Fixed`
  headings.** Both are the merge artifact the checks above describe: a branch
  cut before a release rename re-adds its whole entry, git files both sides,
  and each side brings its heading. Repaired under `## Unreleased`, where they
  were, before they reached a tag.

* **`cgnat <anything>` was accepted, saved, shown back, and applied to
  nothing** ([#273](https://github.com/andrediashexa/hexarouting/issues/273)).
  A catch-all in `do_config` took any line beginning `cgnat `. It passed
  `commit check`, passed `commit`, went into the candidate, was written to
  `/etc/hexarouting/hexarouting.conf`, and was rendered back by `show
  configuration` - and `apply_global` had four branches and no else, so it
  reached the dataplane as nothing at all. The operator was told `commit
  complete` for a setting this box does not have, and then shown it in the
  configuration, which is the one thing that would otherwise have told them it
  was not real.

  Measured on master: `cgnat ha peer 10.0.0.2 priority 200` and `cgnat
  completely made up nonsense` both committed with `failed=0` and both came
  back out of `show configuration`. The issue said such lines appear in no
  `show running-config`; they do appear, and that is worse, because appearing
  is what makes them look like settings.

  The refusal reads `CGNAT_CONFIG`, which is the table `?` offers from, so
  what the box turns away cannot drift from what the box says it takes, and it
  prints that list rather than only saying no. `apply_global` gets the missing
  else as well: `--load` and the binary API reach it without passing the VTY.

* **`commit` and `commit check` printed 28 lines wider than the console they
  are read on** ([#271](https://github.com/andrediashexa/hexarouting/issues/271)).
  [#252](https://github.com/andrediashexa/hexarouting/issues/252) put the
  read-back refusal inside 80 columns and deliberately stopped there; the
  issue that names the rest listed four lines between 84 and 107 columns.
  Driven through the two commands rather than read off the source, the set is
  **28**, from 80 to 231 columns - and the widest of them, a warning printed
  every time a QinQ sub-interface is created, was on nobody's list.

  Every one of them now goes through `console_prose`, or - where it was an
  `err` headline, which wraps nothing - has the verdict on the headline and
  the rest under it. Three refusals gained a row: a statement, a path or a
  configuration line the message used to carry mid-sentence is now quoted
  **whole on a row of its own**, which is the half of #252's rule that says an
  operator greps for that line or types it back in.

  Messages an operator may have scripted against changed shape, which is a
  breaking change to the text and not to any behaviour: `<route>: <next hop>
  is on no connected network` now reads `this next hop is on no connected
  network: <next hop>` with the route on the line below, and the same for
  `<statement>: this box has no interface <name>`. The headline of the SSH
  lockout refusal, of the "nobody can configure this box" refusal and of the
  UDP-export refusal each lost the clause that followed the colon, which is
  now the first line of the prose under it.

* **`commit`'s refusal printed a 178-column line on an 80-column console**
  ([#252](https://github.com/andrediashexa/hexarouting/issues/252)). The
  message that says a commit did not take effect is read on the serial console
  an operator opens because routing is down, and five of its lines were wider
  than that console: 82 and 178 in the read-back refusal, 82, 105 and 116 in
  the branch beside it. The 82 is the evidence for the fix rather than just
  another line over budget - it was wrapped by hand, at what its author took
  for 80 columns, and it was over.

  A message's own prose is now wrapped by measurement against one constant,
  `CONSOLE_COLUMNS`, which the configuration display's existing 80-column
  assertion also spends - two literals is how the refusal came to print 178
  columns while that assertion stayed green.

  **A statement the refusal quotes is printed whole**, past the margin if it
  has to be: not wrapped and not truncated. An operator does two things with
  that line, greps `show configuration` for it or types it back in, and both
  want the bytes contiguous. Truncating is the one that looks cheapest and is
  not - `<- not checked` sits at the end of the line, so the cut removes the
  marker first. What the box guarantees instead is that a statement which can
  fit does fit: 59 columns of statement, and nothing on that line but the
  indent and the mark.

* **`commit` asked the global routing table whether a VRF's static route had a
  reachable next hop**
  ([#255](https://github.com/andrediashexa/hexarouting/issues/255)).
  The check that refuses a next hop on no connected network
  ([#95](https://github.com/andrediashexa/hexarouting/issues/95)) read FIB
  table 0 for every static route in the candidate, `vrf red` ones included, so
  a VRF route was judged against a table it does not forward in - and both
  answers were wrong.

  Measured at a live VPP 26.06 and a live FRR 10.7.1, with `172.31.99.0/24`
  connected only in table 10 and `200.150.0.0/24` connected only in table 0:
  a `vrf red` route via `172.31.99.1` was **refused**, and one via
  `200.150.0.2` was **accepted**. The second is the inert route the check
  exists to catch, committed without a word. The first is the one that costs
  more - a configuration that works, turned away at the prompt, which is the
  harm [#208](https://github.com/andrediashexa/hexarouting/issues/208) was
  filed for.

  The next hop is now resolved in the table the route is in. Which table a VRF
  is, is FRR's answer and not this VTY's: an interface's `vrf` here is a
  number and an FRR block is a name, so the mapping is read from `show vrf`.
  Measured that the number is the one the FIB is keyed by - a kernel route in
  table 10 arrives in VPP as `ipv4-VRF:10`, because `linux_nl` builds the FIB
  from the kernel's own table id.

  **A VRF the box cannot place is reported, not passed over.** An `inactive`
  VRF has no table to resolve anything against, so its routes are named as
  unchecked with the reason. A note and not a refusal: the VRF may be about to
  exist, and refusing a configuration this VTY merely cannot see is the same
  harm again.

* **Three rules of the TCP exporter could be broken with every check green**
  ([#259](https://github.com/andrediashexa/hexarouting/issues/259),
  [#262](https://github.com/andrediashexa/hexarouting/issues/262),
  [#263](https://github.com/andrediashexa/hexarouting/issues/263)).
  `docs/logging.md` states them of `src/plugins/bpa/bpa_log_tcp.c`, and each was
  mutated in the product with all 38 suites, `lab/ipfix-tcp.sh` and
  `lab/ipfix-outbuf.sh` green. The product was right in all three; nothing was
  holding it to being right. `lab/ipfix-outbuf.sh` now asserts all three, which
  is 10 checks it did not have.

  **The order of two records had never been asserted anywhere.** The spill
  **MUST** be prepended on reconnect so that an allocation reaches the collector
  before its release; replacing `vec_insert (outbuf, n, 0)` with `vec_add` put
  82935 bytes of the previous boot's log behind a record written twenty seconds
  later, and every check in the tree stayed green because all of them were true
  of a stream in any order. The lab now finds the new incarnation's own boot
  marker in the collector's log - by the init time the box reports, not by
  position - and requires that no block event arrives behind it. Measured with
  the append: the marker moved from line 6023 to line 2337, six lines after the
  connection was accepted, with 1843 replayed records behind it.

  **Nothing had ever made the spill itself fail.** If it fails the discard
  **MUST** be counted, never silent, and both increments could be deleted
  without a red anywhere. Reaching that code costs a megabyte: the spill only
  triggers above 1 MB, `bpa stop-drain` does not flush the exporter, and a
  counter in a process that is leaving cannot be read afterwards. So the lab
  ends by writing 26000 records with no collector left, three times over - with
  the path writable (1123695 bytes counted as spilled, none dropped), with a
  directory where the file goes (`open()` fails: 1080036 counted as dropped),
  and on a filesystem with no room left (`open()` succeeds and `write()` does
  not: another 1170000 dropped).

  **The stop was already measured in seconds and nothing read the number.** The
  exit path **MUST NOT** wait for the socket; a ten second `poll(POLLOUT)`
  added to `bpa_log_tcp_flush_exit()` took the stop from 1s to 11s, sent not one
  byte, and put the same 82935 bytes in the file.

* **Seven messages sent the operator to `show version` to find out which
  daemons run, and `show version` did not say**
  ([#272](https://github.com/andrediashexa/hexarouting/issues/272)). The
  `commit` branch where nothing took, `show bgp` with no answer, `show ospf`
  twice, `show isis` twice and `configure` on a box still starting all named
  that command; it printed three version strings and nothing about daemons. The
  first of those is printed by a failed commit, on a box an operator may be
  reaching over a serial console, and the one diagnostic step it named did not
  exist - which is worse than naming none, because a reader who finds no
  mention of `bgpd` concludes `bgpd` is fine.

  The destination moved rather than the seven hints, which are now true as
  written. `show version` reports a **running** row of whatever FRR names and a
  **stopped** row of the daemons this VTY's grammar can configure and FRR did
  not name. The stopped row is the half that answers the question actually
  asked: the operator arrived from a message naming one daemon, and a list of
  what is up answers that only by an absence they have to notice.

  An FRR that is **not answering** is reported as that, never as no daemons
  running: those are different faults and the next move differs, and reporting
  the first as the second sends the operator to edit `/etc/frr/daemons` on a box
  that would not have read it. One method asks FRR - `commit`'s existing refusal
  of a `router <protocol>` statement whose daemon is absent now reads the same
  call, so the two answers cannot come to disagree.

  The expected set is read off the command tree instead of listed by hand,
  which is what
  [#187](https://github.com/andrediashexa/hexarouting/issues/187) was: every
  list on the box was missing `ospf6d` while the grammar offered `router
  ospf6`.

### Internal

* **A lab that says it is single-threaded, and a list in another file that
  decides** ([#226](https://github.com/andrediashexa/hexarouting/issues/226)).
  `lab/crossaddr.sh` counts blocks in the order the free list hands them out.
  With workers that list is sharded per worker, so the two subscribers it
  compares would not share a slice and every gap it measures would be about a
  different arrangement - it would print numbers, and they would be about
  something else. That is the defect `lab/extblocks.sh` had at two workers.

  The header said so in a sentence, and `BOTH_WAYS` is in
  `tools/run-all-labs.sh`, with nothing comparing the two: a lab added to that
  list would have carried a comment saying it was not in it. The lab carries a
  `# NOT-BOTH-WAYS:` marker now and `test/test_run_all_labs.sh` reads it, the
  way the suite already holds its `NOT_LABS` reasons as data rather than as
  prose.

  Three reds: the lab added to `BOTH_WAYS`, the marker renamed so the check
  reads nothing, and the marker left with no reason after it.

* **The acceptance test for high availability, written before high
  availability, and red**
  ([#9](https://github.com/andrediashexa/hexarouting/issues/9)).
  `lab/ha-failover.sh` stands up two dataplanes on a shared inside LAN and a
  shared outside LAN, points a subscriber's translated flow at a **service
  address neither box owns**, and then breaks the active box three different
  ways: the uplink pulled with the box still alive, the dataplane stopped the
  way `vpp.service` stops it, and the dataplane killed. Fifteen checks are red
  on `master` and that is the intended result - nothing in this repository
  carries a service address from one box to another. Six further checks, named
  `FIXTURE` in the verdict, are green, and exist so that a broken topology can
  be told apart from a missing feature.

  It is **not** in `tools/run-all-labs.sh`: the reason is recorded in that
  file's `NOT_LABS`, and it says the lab is excluded because the feature does
  not exist yet, not because anything is broken. `lab/crossaddr.sh` is the
  precedent.

  The line the file is built on is `lab/lcp.sh`'s: the lab MAY arrange the
  topology, start both boxes and cause the failure, and it **MUST NOT** put the
  service address anywhere, move it, answer ARP for it, flush the subscriber's
  ARP cache or decide which box is active. A failover lab that moves the
  address itself is testing the lab.

  Two things it turned up, both written into
  [docs/ha.md](docs/ha.md): the VTY has no `vrrp` of its own and hands the
  statement to FRR word for word, where VRRP would run on the `linux_cp` TAP
  and not on the interface the packets are on; and anything at all under
  `nat cgnat` is accepted by `commit check` and `commit` and then silently
  discarded, which is why the lab asserts on the packet and never on the
  configuration being accepted.

* **The exit path counted what it could not write into a process that was
  leaving** ([#270](https://github.com/andrediashexa/hexarouting/issues/270)).
  `docs/logging.md` says a discard MUST be counted and never silent. On every
  other path the counter is enough: the box keeps running and
  `show bpa logging` reads it back. `bpa_stop_drain()` runs from
  `VLIB_MAIN_LOOP_EXIT`, so the counter it increments dies with the process
  moments later - there is nothing left to ask.

  It now says how many bytes were lost, and at **error** level, because a
  `notice` does not reach the process output at VPP's default log level:
  measured on a run that spilled 82,935 bytes, where that line is absent
  entirely. `journalctl -u vpp` is the only place this can still be read after
  the box has stopped, which the message says in as many words.

  Shown on a box whose records filesystem had no room left at the stop:

  ```
  vpp[5497]: bpa: stopping: 458640 byte(s) of connection records could NOT be
  written and are lost. The spill file at /var/log/hexarouting/ipfix-spill.bin
  could not be opened or could not be written to; these records are in no file
  and in no collector, and this is the only place that is said
  ```

  The lab boots the box again afterwards, because a lab that ends with no
  dataplane is reported `NO VPP` whatever it measured - right in general, and
  wrong for the one lab that stops the box on purpose and already has its
  answer.


* **A named FIB table's prefixes were filed under the table above it**
  ([#268](https://github.com/andrediashexa/hexarouting/issues/268)).
  `show ip fib` prints a table with no name as `ipv4-VRF:10` and a table WITH a
  name as `vrf7, fib_index:2`. `parse_fib()` matched only the first, so every
  prefix in a named table landed in whichever table came before it - and
  `Shell.set_vrf()` names every table it creates, so this was every box where
  an operator had typed `interface <name> / vrf <n>`.

  It reported **more** than the box has, which is the hard direction to notice.
  `show route` listed a VRF's routes as the global table's, and the next-hop
  reachability check accepted a static route whose next hop is connected only
  in some other table - the exact silent, inert route that check exists to
  refuse, and the sibling of
  [#255](https://github.com/andrediashexa/hexarouting/issues/255).

  The name-to-id map exists nowhere in `vppctl`: `show ip fib summary` and
  `show ip fib mem` print the same description, and `show fib tables` does not
  exist. It comes from `ip_table_dump`, which carries the id and the name
  together. Reading `vrf<n>` out of the name would work for the tables this
  product happens to create and for no others, which is a guess dressed as a
  rule.

  **A table that cannot be placed keeps its NAME as the key.** Not the table
  above it, which was the defect, and not nothing: dropping the prefixes would
  make a box with no binary API lose a VRF from `show route` without saying so.
  A name is not a number, so no numeric lookup finds it and the answer degrades
  to "not known here" rather than to a wrong one. And `fib_index` is not the
  table id: measured on VPP 26.06, table 10 is `fib_index:1`.

* **`commit`'s read-back refusal says whether this VTY had ever read the
  statement it names**
  ([#227](https://github.com/andrediashexa/hexarouting/issues/227)). A commit
  that FRR did not keep names the statement; it now carries the same
  `<- not checked` mark `show configuration` uses, and one line saying what
  the mark means. Without it the refusal over a statement this box walked
  through its own grammar and the refusal over one it forwarded word for word
  were the same five lines, and those need different first moves: a marked
  statement can be a spelling FRR never understood, an unmarked one points at
  FRR's side.

  The rule that `commit` itself stays quiet about the boundary is unchanged.
  It was measured, not assumed, that the rule does not extend here: the
  twenty-five-statement provider read-back that puts four marks on a
  configuration display produces `commit complete` and no message at all when
  FRR is holding it, so a mark inside this refusal cannot become an alarm that
  is always on.


* **A `commit` refusal named a static route that lives in a VRF as though it
  were in `router static`**
  ([#251](https://github.com/andrediashexa/hexarouting/issues/251)).
  `router static` is this VTY's word for the **global** table. An operator
  told that `router static / route 10.9.0.0/16 10.0.0.9` was dropped went to
  `router static`, found nothing there, and had been sent to the wrong table
  by the one message whose whole job is to name a statement they can find -
  and typing the route where the message said it was would have put it in the
  global table instead of the VRF it was meant for.

  Measured at a live FRR 10.7.1 rather than read off the code: a route typed
  into `vrf red` is read back **inside** that block, indented, still spelled
  `ip route 10.9.0.0/16 10.0.0.9`. The refusal now names it
  `vrf red / ip route 10.9.0.0/16 10.0.0.9`, which is where `show
  configuration` prints it; a route in the global table is still named
  `router static / route ...`.

  **The display renderers never had this wrong.** `show configuration`, `show
  this` and the rollback diff all go through `group_static`, which hoists
  top-level `ip route` lines and only those, and the `<- not checked` marker
  reads `block_paths` for the same reason. The assumption was in
  `as_hierarchy` alone, because it was the one renderer given a statement and
  no position - so the fix is a block path passed in, not a new rule about
  static routes.


* **Ten labs started VPP and never checked that it arrived, so a machine that
  never brought a dataplane up was reported as a defect in the product**
  ([#261](https://github.com/andrediashexa/hexarouting/issues/261)).
  `tools/run-all-labs.sh` classifies a run by grepping its capture for
  `VPP did not start`: a lab that prints it is counted `NO VPP`, which tells
  the reader to look at the machine. Ten labs carried the thirty second wait
  loop and not the line after it that reads the result, so they waited, carried
  on with no box under them, and built a verdict out of empty strings.

  Measured rather than argued. `lab/startup-test.conf` was given one line VPP
  refuses and `sh tools/lab.sh frag` was run whole: VPP exited before creating
  its CLI socket, the lab printed `Could not find VPP CLI socket` forty times
  and ended `LAB FAILED: 14 check(s)`, and `lab_no_vpp()` run verbatim over
  that capture agreed it was a product failure. A release gate reads that
  count.

  **The classifier's fallback cannot cover this and was never meant to.** It
  looks for `connect: Connection refused`, which is what a socket with nothing
  behind it says: a VPP that started, created its socket and then died. A VPP
  that never started leaves no socket to refuse. The guard line is the only
  thing that classifies a dataplane that never came.

  The wait itself and the thirty seconds are unchanged. `lab/lib.sh` grows one
  `wait_vpp` helper carrying both halves, and the ten labs use it; the forty
  start sites that already print the line keep their own copy, because
  converting them would move the input of every lab in the suite in order to
  change no lab's verdict. `test/test_lab_workers.sh` holds the guarantee
  rather than the spelling: it sweeps every `vpp -c` in `lab/` and goes red on
  a start site with neither the helper nor the line between it and the next
  one, and it runs `wait_vpp` against a stub `vppctl` to pin that the line it
  prints is the line the classifier looks for.

* **The labs' `vppctl` carriage returns, swept and pinned**
  ([#249](https://github.com/andrediashexa/hexarouting/issues/249)). `vppctl`
  ends every line with CR+LF, and neither `awk`'s field splitting nor `sh`'s
  IFS counts `\r` as whitespace, so the carriage return stays attached to the
  **last field of every line**. A value read off the end of a line is
  `600<CR>` while the terminal shows `600`.

  **Which half of a lab survives that was measured, not assumed**, under
  `dash`, which is the shell `tools/lab.sh` runs the labs with: `[ "600<CR>"
  -eq 600 ]` is **true**, because the number parser calls `isspace()` and
  `isspace('\r')` is true, while `[ "600<CR>" = "600" ]` is **false**, a
  `case` glob does not match and `grep -x` does not match. Every arithmetic
  comparison survives it; every string comparison does not.

  It fails in the direction that hides. A needle that can never match makes
  "this must appear" red on a correct box, which somebody notices, and "this
  must NOT appear" green on a broken one, which nobody does.

  **All 54 labs were swept**: 1107 assertion sites, of which 280 read a value
  derived from `vppctl`. **None of the 280 was bitten**, and that is the
  measurement, not an absence of one: their needles are literal strings, which
  have no carriage return to lose, and their numeric comparisons are the ones
  `dash` tolerates. The single bite the sweep found sits just outside the
  assertion set - a printed cross-check in `lab/snmp.sh` comparing the SNMP
  answer against the dataplane with `tr ' ' '\n' | grep -x 600`, which
  compares whole lines, so it never matched and the `|| echo 600` behind it
  printed the literal. The report said "vppctl says 600" whatever the box
  held. It is now an assertion against the value read off the dataplane, and
  it goes red with the strip removed **while the printed line still reads
  `600`**, which is the whole difficulty in one screen.

  **Where the strip belongs was decided by counting.** A wrapper every lab
  pipes `vppctl` through is the obvious answer and was rejected: the needle of
  an `expect_in`/`expect_not_in` is a literal string in all but a handful of
  those 280 sites, and a literal needle has no carriage return to lose - it
  matches inside `...bpa-in2out<CR>` like any other substring. Wrapping
  roughly a thousand call sites would move the input of every lab in order to
  change no lab's verdict, and each one would then have to be shown not to
  have moved. So the strip goes where the value is **extracted**, in one named
  helper - `vc()` in `lab/lib.sh` - and `test/test_lab_crlf.py` refuses the
  extraction shapes that carry a carriage return at all: the last `awk` field,
  a whole line, `grep -x`, a `sed` that prints the rest of a line, an
  end-anchored regex. Seventeen sites were converted, including the sixteen
  that were verdict-neutral, so the rule has no allowlist for anybody to grow.

  **The guard reads two steps, not one.** A lab can park a whole output in a
  variable on one line and extract from it on another, and the second line
  never says `vppctl` - `lab/icmp-tcp.sh` was doing exactly that. What it does
  not follow is stated in its own docstring rather than left to be discovered:
  a value through a file, through a function's return, or built by string
  arithmetic is out of reach of a source check.

* **A dead `expect_not_in` in `lab/vty.sh`**, found on the way and the same
  defect class with no carriage return in it
  ([#249](https://github.com/andrediashexa/hexarouting/issues/249)). The lab
  asserted `expect_not_in "BGP_BLOCK: cgnat"`. Its generator prints that label
  followed by the offered words **sorted**, so the label is only ever followed
  by the alphabetically first one, and the assertion nine lines above pins
  that word to `address-family`. `BGP_BLOCK: cgnat` could not match on a
  leaking box either, so the check had been green since the day it was
  written. It now reads the word list. Shown failing by leaking `cgnat` into
  the BGP block's grammar, which the old spelling stays green on. The two
  neighbouring negatives are **not** dead: `cgnat` sorts before `description`
  in `IN_BLOCK` and `address-family` before `bfd` in `BGP_STMT`, so a leak
  there does reach the first position.


* **A `NO VPP` verdict now carries VPP's own account of why**, instead of one
  line with nothing under it
  ([#253](https://github.com/andrediashexa/hexarouting/issues/253)). `FAIL`
  prints its red checks and `NOT MEASURED` prints the reading it missed, so
  the one state whose whole meaning is "look at the machine, not at the
  product" was the one that gave the reader nothing to look at. It cost a
  release run: `extblocks+workers` came back `NO VPP`, the same lab passed
  alone minutes later, and nothing in the run could say whether the 30 second
  wait had expired or the dataplane had died - which are opposite jobs.

  The report now names which of the two it was and prints the log underneath:

  ```
    extblocks+workers  NO VPP - the dataplane never came up
       the dataplane was gone by the verdict. VPP's log:
         NO DATAPLANE at the verdict: vppctl cannot reach VPP. Last of /tmp/vpp.log:
           | vpp[20]: received signal SIGSEGV, PC 0x7f2a1c0b4e21
  ```

  `tools/lab.sh` gives each lab a container it throws away, so `/tmp/vpp.log`
  died with it and stdout is the only channel out. `lab/verdict.sh` therefore
  prints the tail of that log whenever a lab ends **without passing** and
  `vppctl` can no longer reach a dataplane - not on a pass, because
  `ipfix-stop`, `ipfix-outbuf`, `oom` and `snmp` each stop VPP on purpose. Ten
  labs start VPP with no wait guard at all, `icmp-tcp` among them, so this is
  the only place their log can reach a reader.

  **The marker is evidence and not a verdict.** A lab that measured something
  and then lost VPP is still reported `FAIL`: calling that a machine problem
  would hide a real crash, which is the failure the classifier exists to
  avoid.

  **The job count and the 30 second wait are unchanged, deliberately.** The
  capture on the issue shows VPP answering `create host-interface` and every
  call after it refused, so the wait did not expire and raising it would have
  changed nothing. `extblocks` with two workers then passed under a Docker CPU
  quota of 1.0 **and** of 0.25 cores, so CPU shortage alone does not reproduce
  the failure either. Nothing measured supports turning either knob, and the
  next `NO VPP` will carry the line that says which one to turn.


* **A record could go out ahead of its template and every check stayed green**
  ([#258](https://github.com/andrediashexa/hexarouting/issues/258)).
  `docs/logging.md` says events **MUST** be held while `last_template_sent` is
  zero, because a data set that reaches a collector before its template has no
  layout and cannot be read. Removing that guard from `bpa_log_drain()` put
  four block events and the boot marker on the wire ahead of the template, and
  all 38 suites and every one of `lab/ipfix.sh`'s checks stayed green.

  **The oracle was the thing forgiving the defect.** `tools/ipfix_collector.py`
  holds a data set that arrives without its template and reprocesses it when
  the template comes - which the same document says a serious collector SHOULD
  do, and which is how the ordering was found in the first place. So the
  records all arrived, decoded and paired, and no count anywhere moved. The
  collector now **counts** what it had to hold and prints the total, and
  `lab/ipfix.sh` requires it to be zero.

  **The collector is started before the exporter is configured**, which is half
  the fix: one started afterwards cannot tell a box that sent a record too
  early from a collector that joined the stream too late, because both read as
  a data set with no template.

  The window is the first template of a VPP process, so it opens on every boot
  and on the first `cgnat logging enable` after one - and what falls in it is
  the allocations of every subscriber reconnecting at once, plus the boot
  marker a collector needs to tell an allocation still in force from one the
  box died holding.

* **Nothing measured the zero in "zero records in deterministic mode"**
  ([#266](https://github.com/andrediashexa/hexarouting/issues/266)). It is the
  answer `README.md` gives to *Why*: per-flow NAT logging produces terabytes,
  and BPA reduces that to one record per block, or to zero in deterministic
  mode. `docs/context.md` invariant 1.2 says the same from the other side. An
  ISP chooses the mode for exactly this, and **every IPFIX lab in the
  repository runs a dynamic pool** - `ipfix`, `ipfix-tcp` and `ipfix-stop` all
  say `mode dynamic`, and nothing had ever armed an exporter over a
  deterministic one.

  `lab/det-zero.sh` does. Four subscribers translated on a deterministic pool,
  **zero records emitted**; then the same box, the same exporter, the same
  collector and the same traffic pointed at a dynamic pool, which produces
  three records decoded on the wire with their port ranges.

  **That second half is the lab.** A box whose exporter was never armed also
  emits zero and would satisfy the claim while proving nothing, which is the
  control `lab/trail-fsync.sh` and `lab/localaddr.sh` both carry.

  Shown failing by making the deterministic branch of `bpa_in2out.c` log the
  way the dynamic one does: four records, and the collector printed the
  deterministic subscribers' own allocations back - the year of records the
  mode exists to avoid. And shown failing the other way, with the exporter
  never armed: the claim stayed green and the control went red, which is what
  the control is for.


* **The rule that a secret must not enter this repository was held only for the
  four directories the ISO carries.** `CONTRIBUTING.md` section 2 says secrets
  "MUST NOT go into this repository either", and
  [#245](https://github.com/andrediashexa/hexarouting/issues/245)'s check read
  `src`, `appliance`, `tools` and `docs` - what `build-iso.sh` copies. A private
  key in `lab/` never reaches an ISO and is published just as thoroughly: this
  repository is public, and so is the wiki mirror inside it.

  The scope is now every directory in the tree, **enumerated rather than
  listed**. A list is something somebody has to remember to extend, and the two
  defects this check sits between - a lab excluded from every run
  ([#233](https://github.com/andrediashexa/hexarouting/issues/233)) and a test
  file nothing compiled
  ([#235](https://github.com/andrediashexa/hexarouting/issues/235)) - were both
  exactly that. Nine directories today, and one added next year is covered
  without anybody deciding to cover it. Nothing in the tree is caught, which is
  the answer wanted.

  The refusal also stopped saying **"credential in the image"** while somebody
  is looking at `lab/`. One script answers two questions now and names which
  one it was asked, because a message that points at the wrong object is the
  defect this session has spent the most time on.

* **The control that keeps the console driver honest was invoked by nothing**
  ([#256](https://github.com/andrediashexa/hexarouting/issues/256)).
  `lab/install-iso.sh` has a `wrong-password` mode, `TESTING.md` calls it *how
  the driver is kept honest*, and nothing ran it: not the suite, which excludes
  `install-iso`, not the `Makefile`, and not the release gate. `make
  test-install` runs it now, in the same container, and requires it to fail
  **at the login**.

  What it controls is why it matters. `TESTING.md` states the failure in the
  same paragraph: a login that fails silently would run the whole session
  against a login prompt and report a pass. `lab/install-iso.sh` is the only
  place a box somebody receives is installed and booted.

  **The check that holds the wiring was wrong first, and the second version is
  the entry.** It grepped the `Makefile` for the phrases the recipe prints, and
  passed on a recipe whose inversion had been replaced by `if false`: every
  string was still there, inside a branch that could not be reached. A check
  that reads the text of a decision cannot see the decision. The recipe is now
  extracted and RUN against a stub that behaves each of the three ways, and
  what is asserted is its verdict.

  **And the mutation that exposed that was not enough either.** Deleting the
  inversion still failed a run printing `LAB PASSED`, because the second check
  rejects output with no refusal diagnosis in it - so the mutation read as
  harmless. The case where only the inversion can catch it is a control that
  **exits 0 with the refusal still on the screen**: the words are not what says
  the box refused, the exit status is. That case is now driven.

  The `make test-install` target itself has not been executed here: it builds
  an ISO and boots two VMs, and a release suite had the machine. Its three
  branches were driven against the stub instead, which is what the section
  above describes.

* **Not one mapping vector had ever used a port range other than the default.**
  `docs/context.md` invariant 1.3 says there are two specifications of the
  mapping and they MUST agree, and `test/test_bpa_pool.c` compares them over
  thousands of vectors. Every one of those vectors was generated from a pool
  with `port_min` 1024 and `port_max` 65535, so `port_min` was a constant in
  the comparison rather than a variable, and the two specifications had never
  been asked whether they agree about it.

  A sixth vector pool, `narrow-ports`, uses 2048-20479 with a 256 port block.
  Measured against the plugin: **they agree**, over 3,296 forward, 9,888
  reverse and 486 stride comparisons. There was no defect hiding here; there
  was no evidence either.

  **The red is the point.** Hardcoding `port_min` to 1024 where the block's
  ports are computed - which is what the code effectively read for every pool
  that existed before - fails 10 vectors, and **the lowest failing index is
  3040, the first vector of the new pool.** Every one of the 3,039 before it
  agreed with the broken implementation.

  Building the pool cost me the same defect one layer up: the stride vectors
  were generated from a pool built WITHOUT the port range, so they disagreed
  with a plugin that was right, and the generator was at fault rather than the
  product. The comment at that line says so.

* **The collector behaviour that made a defect visible was held by nothing**
  ([#257](https://github.com/andrediashexa/hexarouting/issues/257)).
  `docs/logging.md` says a serious collector buffers a data set that arrives
  without its template and reprocesses it when the template appears, says
  `tools/ipfix_collector.py` does, and then says why it matters: *that is how
  the ordering became visible instead of looking like vanished records.* The
  reprocessing loop could be disabled with all 38 suites green.

  The collector ships: `appliance/install.sh` puts it on the box as
  `/usr/lib/hexarouting/ipfix-collector`. And the labs that drive it assert
  record counts, so without the reprocessing they would have gone red saying
  **"expected 2 records, got 0"** - which names the box, and the box would have
  been right. A message that names the wrong component is the same defect this
  release keeps finding, one level out.

  Four assertions in `test/test_collector_marker.py`, driving real IPFIX bytes
  rather than calling an internal, because the ordering is a property of what
  arrives on the wire. Two of them are controls: the report has to SAY it is
  holding a record, so silent loss and a deferred template do not look alike,
  and a record arriving after its template still has to decode, or the section
  would pass on a collector that decodes nothing at all.

* **The release gate refuses a note that does not record the boot and the
  install** ([#260](https://github.com/andrediashexa/hexarouting/issues/260)).
  `CONTRIBUTING.md` section 5 says the release note MUST record that the medium
  was built, booted, installed and booted again. The gate PERFORMS both and
  never read the note, and the rule decayed across two releases without a word:
  `v0.64.0` carries the sentence, `v0.65.0` has one of the words, and
  `v0.66.0`, the most recent release, says nothing about either.

  It is the note and not the steps, because the steps are already refused if
  they fail. The note is what an operator who does not clone this repository
  reads, and section 5 says so one line above: a tag is a pointer, a release is
  where they read what changed. A note that does not say the medium was
  installed and booted leaves that operator to assume it, and
  [#119](https://github.com/andrediashexa/hexarouting/issues/119) and
  [#121](https://github.com/andrediashexa/hexarouting/issues/121) are what the
  assumption would have been wrong about.

  **The red needed no fixture: `v0.66.0` is in this repository and the check
  refuses it.** All four words on one line, the way `v0.64.0` names them,
  because loose matching passes on any note that mentions an installed box -
  which every note about this product eventually does. It then refused
  the `v0.67.0` tag written without mentioning the boots, which is the
  only reason that tag says it.

* **A release section could carry the same heading three times, and 0.67.0
  does** ([#267](https://github.com/andrediashexa/hexarouting/issues/267)).
  `CONTRIBUTING.md` says the entry goes *in the order a reader cares about*,
  and 0.67.0 ships with `### Fixed` three times and `### Added` twice. Nobody
  wrote it that way: it comes from merging fourteen branches that had each
  added under the same heading, where git sees two additions at one anchor and
  the resolution keeps both sides with their headings.

  The gate already refuses a CHANGELOG that says the same thing twice, and the
  comment above that check describes this exact mechanism - it compares
  **bullets**, and a duplicated heading is the same accident one level up. It
  compares headings now, within a section, naming which release and which line.
  The red needed no fixture either, and it found two older releases carrying
  it too.

## 0.67.0 — nine MUSTs nothing was holding, and an ISO that shipped its own keys

[#55](https://github.com/andrediashexa/hexarouting/issues/55),
[#63](https://github.com/andrediashexa/hexarouting/issues/63),
[#83](https://github.com/andrediashexa/hexarouting/issues/83),
[#141](https://github.com/andrediashexa/hexarouting/issues/141),
[#145](https://github.com/andrediashexa/hexarouting/issues/145),
[#163](https://github.com/andrediashexa/hexarouting/issues/163),
[#172](https://github.com/andrediashexa/hexarouting/issues/172),
[#187](https://github.com/andrediashexa/hexarouting/issues/187),
[#197](https://github.com/andrediashexa/hexarouting/issues/197),
[#210](https://github.com/andrediashexa/hexarouting/issues/210),
[#213](https://github.com/andrediashexa/hexarouting/issues/213),
[#217](https://github.com/andrediashexa/hexarouting/issues/217),
[#218](https://github.com/andrediashexa/hexarouting/issues/218),
[#219](https://github.com/andrediashexa/hexarouting/issues/219),
[#220](https://github.com/andrediashexa/hexarouting/issues/220),
[#221](https://github.com/andrediashexa/hexarouting/issues/221),
[#222](https://github.com/andrediashexa/hexarouting/issues/222),
[#223](https://github.com/andrediashexa/hexarouting/issues/223),
[#224](https://github.com/andrediashexa/hexarouting/issues/224),
[#225](https://github.com/andrediashexa/hexarouting/issues/225),
[#226](https://github.com/andrediashexa/hexarouting/issues/226),
[#228](https://github.com/andrediashexa/hexarouting/issues/228),
[#229](https://github.com/andrediashexa/hexarouting/issues/229),
[#230](https://github.com/andrediashexa/hexarouting/issues/230),
[#231](https://github.com/andrediashexa/hexarouting/issues/231),
[#232](https://github.com/andrediashexa/hexarouting/issues/232),
[#233](https://github.com/andrediashexa/hexarouting/issues/233),
[#234](https://github.com/andrediashexa/hexarouting/issues/234),
[#235](https://github.com/andrediashexa/hexarouting/issues/235),
[#236](https://github.com/andrediashexa/hexarouting/issues/236),
[#237](https://github.com/andrediashexa/hexarouting/issues/237),
[#238](https://github.com/andrediashexa/hexarouting/issues/238),
[#239](https://github.com/andrediashexa/hexarouting/issues/239),
[#240](https://github.com/andrediashexa/hexarouting/issues/240),
[#241](https://github.com/andrediashexa/hexarouting/issues/241),
[#242](https://github.com/andrediashexa/hexarouting/issues/242),
[#243](https://github.com/andrediashexa/hexarouting/issues/243),
[#244](https://github.com/andrediashexa/hexarouting/issues/244),
[#245](https://github.com/andrediashexa/hexarouting/issues/245),
[#246](https://github.com/andrediashexa/hexarouting/issues/246),
[#247](https://github.com/andrediashexa/hexarouting/issues/247).

### Added

* **`commit` refuses a route-map that is referenced and never defined**, and
  names it ([#145](https://github.com/andrediashexa/hexarouting/issues/145)).
  FRR takes the statement, warns once and denies everything it governs: the
  session comes up Established, carries **zero** prefixes, and nothing on the
  box says why. Measured on FRR 10.7.1 with two speakers over a veth pair,
  asserted on the receiving side's table - `neighbor X route-map RM-MISSING
  in` holds zero where the same configuration with the map defined holds both
  prefixes, and `redistribute ... route-map` and `network ... route-map`
  originate nothing. Those three shapes are refused and no others.

  **The warning it replaces was reaching nobody.** FRR prints `The route-map
  'X' does not exist.` to the vtysh session; this VTY hands whole
  configurations to `frr-reload.py`, whose output is filtered to lines
  carrying `Exiting:`, `ERROR`, `% `, `Failed` or `Exception:`. That line is
  none of them.

  **A `call` to an undefined map is reported and not refused**, because it was
  measured to behave differently: FRR skips a call it cannot resolve, the
  clause continues, and the prefix is still advertised. So is every other
  place a map can be named, where the cost was not measured - and so is a map
  that is defined and used by nobody, which is what an operator building a
  policy map-first has on the box between two commits.

  **Refused only when this change is what causes it**, the guard the SSH
  lockout checks already use. A box whose saved configuration already carries
  the reference is warned and can still be worked on: `--load` runs this same
  check at boot and refuses the file whole, so refusing there would bring the
  box up with nothing on it, which is worse than the fault.

* **`commit` says when a change would stop advertising the prefix carrying
  the operator's own session**, and requires `commit cutoff`
  ([#145](https://github.com/andrediashexa/hexarouting/issues/145)). The SSH
  lockout check refuses a configuration nobody could log in with
  ([#35](https://github.com/andrediashexa/hexarouting/issues/35)); this is the
  same failure one layer down, and the box is the only thing that can see it
  coming. The address comes from `SSH_CONNECTION`, whose **server** half is
  the address this box advertises a route to.

  Gated rather than refused, the shape
  [#23](https://github.com/andrediashexa/hexarouting/issues/23) established:
  withdrawing a prefix on purpose is ordinary, and the box cannot tell that
  apart from the mistake. It is then applied as `commit confirmed 10` whether
  or not that was asked for, because the word acknowledges the consequence and
  does not provide a way back.

  **It fires only when it is sure.** Every neighbour that has an outbound
  policy must deny the prefix and none may be unreadable: a `match` on a
  community or an AS-path, a `call`, a `distribute-list`, a `filter-list`, or
  an origination `bgp_originations()` is blind to withdraws the whole finding.
  Measured on FRR 10.7.1 against the peer's table, a route-map's **implicit
  deny** withdraws a prefix as surely as a deny clause naming it, which is the
  case an operator does not see by reading their own policy back.

  **On a serial console it says it could not check**, rather than passing
  silently - and only when the change moves something that decides what this
  box advertises.

* **`block-stride`: the distance between the blocks one subscriber holds**
  ([#55](https://github.com/andrediashexa/hexarouting/issues/55)). An
  extension used to be whatever came off the free list next. With a stride,
  the nth block a subscriber holds starts `n × stride × block-size` ports
  above its first, **on the same public address**: a stride of one puts them
  side by side, which is one range for an investigator instead of several, and
  a larger one spreads the subscriber across its own address's port range,
  which is what makes it harder to profile a customer by watching a single
  range. Neither is obviously right, so it is a setting.

  **The default is zero and zero is what the box has always done**, so an
  upgrade changes nothing - asserted in `ref/validate.py` and in
  `test/test_bpa_pool.c` rather than promised: with no stride the model
  predicts no block at all, because today's answer is a function of the
  pool's history and not of its definition.

  **It is not part of the mapping.** `bpa_forward()` does not read it and must
  not: 40 pool-and-stride combinations in C and 32 in the model move zero
  subscribers, which is what keeps a stride out of the capacity-change
  procedure ([#23](https://github.com/andrediashexa/hexarouting/issues/23)) -
  a change that moved even one would belong there. `commit` is unchanged and
  reports no remap for it, and that is the measurement rather than a claim.

  **Every block is still its own RFC 8158 `natEvent 16`, wherever the stride
  puts it.** `lab/extblocks.sh` now runs a real IPFIX collector and asserts
  two records for a subscriber holding two blocks, each naming its own port
  range - the strided one included, which is the range a wrong implementation
  would log as the one the allocator would have given.

  **A stride is a preference and never costs a customer traffic.** When the
  block it names is held by somebody else, or the subscriber sits at the end
  of its public address, the allocator falls back to the free list and counts
  it. `show bpa blocks` prints both numbers, because a stride that is
  configured and never honoured looks from outside exactly like no stride at
  all.

  **A stride the worker sharding cannot honour is warned about, not refused.**
  Blocks are partitioned by `block_idx % n_workers` and a subscriber draws
  only from its own worker's slice, so a stride whose step is not a multiple
  of the worker count names a block this worker may not hand out - for every
  subscriber rather than for some. The box says so at pool creation and names
  the nearest stride that works. It is a warning because the worker count is a
  startup setting: a refusal would leave a pool that came up yesterday failing
  to come up after a restart, and a pool that does not exist is every
  subscriber on it losing traffic, which is not an acceptable price for a
  preference about layout.

* **`show bpa pool` prints the range of ports a subscriber can reach**, in
  ports, which is the unit an operator sells in. `block-size 64` with
  `max-blocks 8` already **is** a per-pool range - a subscriber starts at 64
  ports and grows one block at a time, only when it fills one, as far as 512 -
  and nothing said so.
[#225](https://github.com/andrediashexa/hexarouting/issues/225).

* **`show cgnat alg` and `show cgnat forward`**, which the dataplane had and
  this VTY could not reach. Of fourteen `show bpa` commands the plugin
  registers, twelve were reachable; the two that were not are the two that
  answer *what does the box actually have* rather than what the file says -
  whether an ALG is on **in the dataplane**, and which port forwards the
  dataplane holds. The way to that answer was `vppctl`, which this VTY exists
  to keep an operator out of and which is every capability at once
  ([#228](https://github.com/andrediashexa/hexarouting/issues/228),
  [#76](https://github.com/andrediashexa/hexarouting/issues/76)).

  It is the shape of [#221](https://github.com/andrediashexa/hexarouting/issues/221)
  and [#224](https://github.com/andrediashexa/hexarouting/issues/224) one step
  over: those were things the box does that an operator cannot find; this is a
  thing the box **says** that they cannot hear from here. And the distinction
  the two commands draw is
  [#172](https://github.com/andrediashexa/hexarouting/issues/172)'s: the file
  and the box come apart quietly, and only reading the box says so.

### Changed

* **The outbound rewrite carries its checksum corrections on the session
  instead of recomputing them per packet.** `bpa_rewrite_src` was the largest
  remaining piece of what `bpa-in2out` costs per packet, about half of it in
  the IPv4 header checksum and half in the L4 one. The correction is constant
  for the life of a session - the subscriber always sends from the same address
  and port, and the session always rewrites them to the same pair - so it is
  worked out once at session creation and added per packet.

  **`bpa-in2out` goes from 470 to 346 clocks per packet, a 26% reduction.**
  Three alternating pairs on one Xeon E5-2680 v4 at 2400 MHz, 64-byte frames,
  one session, 256 vectors per call, rebuilding between every arm:

  | pair | before | after | baseline `ip4-input` | `ip4-input` with the CGNAT |
  |---|---|---|---|---|
  | 1 | 468 | 345 | 21.2 / 20.6 | 32.1 / 32.4 |
  | 2 | 469 | 343 | 21.2 / 20.9 | 32.6 / 32.0 |
  | 3 | 472 | 351 | 21.2 / 21.2 | 32.3 / 34.1 |

  Alternating, and with both of the lab's own load witnesses printed, because a
  single pair proves nothing on a machine other work shares: the first "after"
  run taken on this branch read 440 with `ip4-input` at 51, which is a busy
  box, not a result. At 2400 MHz this is 5.11 Mpps per core before and 6.93
  after, so 100 Gbps of 64-byte frames is about 22 cores in this node rather
  than 29.

  This optimisation was written once before, measured at a 28% saving and
  reverted for producing `ipsum=BAD tcpsum=BAD` on every translated packet. The
  reason, settled against VPP 26.06's own header and printed by
  `test/test_bpa_rewrite.c`: **`ip_csum_add_even()` SUBTRACTS.** VPP names its
  two primitives for what happens to the DATA, while the value they operate on
  is the checksum, which is the one's complement of the sum over that data - so
  `ip_csum_add_even` subtracts and `ip_csum_sub_even` adds. A delta applied
  with the one whose name reads like addition lands with the wrong sign and
  misses by exactly twice itself, on 199988 of 200000 random packets.

  Three further ways to get it wrong are asserted rather than argued: the
  corrections for the IPv4 header and for L4 are NOT interchangeable (the
  pseudo-header carries the ports, the IPv4 header does not), an ICMP echo
  takes the identifier and no address at all (RFC 792: no pseudo-header), and
  operands must be converted to network order before the correction is built.

### Fixed

* **Nothing stopped the ISO build from shipping an answer file, and the ISO it
  did ship carried three SSH host private keys.** `docs/appliance.md` says the
  build **MUST NOT** put an answer file in the image, because an answer file
  carries the root password and an ISO is published: it goes on a USB stick, on
  a download page, into a customer's hands. Writing one into the root
  `appliance/build-iso.sh` was about to squash left all 34 suites green.
  `appliance/no-credentials-in-image` is the refusal, run on the root before
  `mksquashfs` and on the ISO tree before `grub-mkrescue` - the ISO tree is the
  second one because `hexarouting.answers` with no argument reads the root of
  the **medium**, not the squashfs. It refuses an answer file by name and by
  content, a private key, a non-empty `authorized_keys` and an `/etc/shadow`
  entry that is not locked. An answer file by content means **every** line
  parses as an install answer, which is what keeps the rule off
  `hexarouting-install` and off the document, both of which describe the format
  at length.

  **Running it against a real image found the exposure the rule was written
  about, in the product rather than in a mutation.** `hexarouting-0.37.0`
  carried `/etc/ssh/ssh_host_{rsa,ecdsa,ed25519}_key`, generated by
  `openssh-server`'s postinst inside the build container and squashed in with
  everything else - the public halves still name `root@a3bcac8dc76e`. The
  installer unpacks that squashfs onto the disk and then enables `sshd` with
  `PermitRootLogin yes`, so every appliance installed from one release answered
  with the same host key whose private half was in the download. The build now
  removes them and `hexarouting-install` runs `ssh-keygen -A` on the target,
  failing the install if it cannot: a box whose `sshd` will not start has no
  management path, and both halves are asserted on the artefact by
  `lab/install-iso.sh`.

  **Breaking change for anything reached over SSH by host key.** A box
  installed from a release before this one holds a host key that is not unique
  to it; reinstalling, or `rm /etc/ssh/ssh_host_*_key && ssh-keygen -A &&
  systemctl restart ssh`, gives it its own and changes the fingerprint the
  operator's client has pinned.

  The split is deliberate. `test/test_no_credentials.sh` drives the refusal
  against fake roots in `make test`, because reading a real ISO means building
  one - root, a privileged container, ten minutes - and it also asserts that
  `build-iso.sh` calls the refusal and calls it **before** the two commands
  after which the result is no longer a directory, since a fast check that
  never reads an image cannot tell a build that runs the refusal from one that
  stopped calling it. `lab/install-iso.sh` now unpacks the whole squashfs
  rather than one file out of it and runs the same script over the image that
  was actually built. Measured: 1.0 s over the 16,965 files of a real image,
  and 2.5 s to unpack it
  ([#245](https://github.com/andrediashexa/hexarouting/issues/245)).

* **Nothing stopped `hexarouting.service` from growing an `ExecStop` that
  deletes every pool.** `docs/appliance.md:470` says stopping the service
  **MUST NOT** delete production pools and the unit repeats it in a comment;
  adding `ExecStop=/usr/bin/hexash -c "delete nat cgnat ; commit"` left all 34
  suites green, because `test/test_install_manifest.py` asserts the unit is
  installed and never reads a byte of it. The cost is not a stopped box:
  `hexarouting.service` carries `PartOf=vpp.service`, so a stop-time command
  there fires on every RESTART, and the failure would arrive during an ordinary
  upgrade on a box that had been working.

  **The rule is not "no `ExecStop`", because this product needs one.**
  `vpp-stop-drain.conf` runs `vppctl bpa stop-drain` before the signal so the
  blocks the dataplane holds are closed in the retention log while the graph is
  still running to carry the records
  ([#217](https://github.com/andrediashexa/hexarouting/issues/217)). Nor is it
  "not on that file", which is a rule about where a line is typed and is silent
  on the mutation that matters most - the drain file itself edited into a
  deletion. So `test/test_stop_is_not_a_teardown.py` draws the line on **what
  the command does**: a stop-time command MAY ask a running process to flush
  state it is already holding, and MUST NOT reach the configuration surface. A
  drain is idempotent and takes nothing away that an operator put there; a
  deletion is the opposite in every direction, and at the moment it runs the
  box cannot tell them apart.

  Every stop-time directive anywhere under `appliance/` - not only in
  `appliance/systemd/`, so a unit written by `install.sh` is covered too - must
  be in the suite's table byte for byte with its reason, **and** must still pass
  the classifier, so blessing a line is not enough to ship it. Shown red six
  ways: the sweep's own mutation, the same as an `ExecStopPost`, a unit the
  installer writes, the drain edited into a deletion with the table edited to
  match, the drain removed (which is #217 reopening), and the rule deleted from
  `docs/appliance.md`. A seventh mutation gave zero red and the check was the
  thing that was broken: `\bExecStop` cannot match inside
  `printf '[Service]\nExecStop=...'`, because the character in front is the
  "n" of a literal `\n`
  ([#238](https://github.com/andrediashexa/hexarouting/issues/238)).

* **The boot self-test's `exit 0` was unasserted, and so was the unit that
  decides whether it matters.** `docs/appliance.md:552` says the diagnostic
  **MUST NOT** bring down the boot, and the whole of that promise was the last
  line of a 1000-line shell script: changing it to `exit "$fail"` left all 34
  suites green. The self-test makes around 141 checks, several of which go red
  on a healthy box in an unusual but supported configuration, so a status that
  carried the verdict would stop a box in a customer's rack because a
  diagnostic disagreed with it, at the one moment its console is what somebody
  needs. `test/test_selftest.sh` now reads the status of all 86 runs it already
  makes, with a control asserting those runs produced `[FAIL]` lines, since
  exiting zero out of boxes that all passed would prove nothing.

  **The systemd half was the unheld one.** `hexarouting-selftest.service`
  carried `ExecStart=` with no leading `-`, so systemd was told to treat the
  diagnostic's verdict as the unit's. Measured on systemd 252 with transient
  units of that exact shape: exit 3 gives `Result=exit-code` and
  `ActiveState=failed`, `SIGSEGV` gives `Result=signal` and
  `ActiveState=failed`, and a consumer carrying `Requires=` on either never
  starts. The console getty is ordered after this unit (#93) and survives only
  because it says `Wants=`. The unit now carries `ExecStart=-`, which absorbs
  both a non-zero status and a signal while still recording `ExecMainStatus`,
  and the suite asserts that, `Type=oneshot`/`RemainAfterExit=yes`, the absence
  of `Restart=`, and that nothing under `appliance/` declares `Requires=`,
  `Requisite=`, `BindsTo=` or `PartOf=` on the unit. The comment on the `exit`
  line was also in Portuguese, against `CONTRIBUTING.md` section 1 rule 1
  ([#244](https://github.com/andrediashexa/hexarouting/issues/244)).

* **`commit` said it had rolled a failed commit back, and nothing asserted that
  it had.** `docs/routing.md:1528` states one requirement in two halves -
  `commit` MUST validate before applying, and MUST restore the previous state
  if application fails halfway - and only the first half had a check behind it.
  Deleting the restore left all 33 suites green. That matters because `apply()`
  removes pools before it adds them, so the window is the ordinary shape of
  every commit that changes a pool: a dataplane that accepts the check and
  refuses the write leaves the box holding neither configuration, with every
  subscriber on the removed pool disconnected and the VTY printing *"rolling
  back to the previous state"* over the top of it - a report of success with no
  effect behind it, which is the defect class this repository is organised
  against. `test/test_hexash_apply.py` now models the dataplane with STATE,
  because an API that only records calls cannot answer what the box is HOLDING
  when the commit has finished failing, and asserts the end state with a
  control: the apply must first be shown to have taken the running pool off the
  box, since "restored" and "never touched" are indistinguishable from the end
  state alone. Shown red three ways - the restore deleted, and the removal
  suppressed
  ([#237](https://github.com/andrediashexa/hexarouting/issues/237)).

* **A translated UDP datagram could leave with a checksum of wire zero, which
  means "not computed".** The per-packet form folded a 64-bit accumulator, and
  an accumulator lands on exactly zero whenever `stored + old_address +
  old_port == new_address + new_port` as plain integers. RFC 768 requires a
  computed zero to be sent as `0xffff`; sending it as zero tells the receiver
  not to check the very thing that was just rewritten. The session-carried
  correction cannot reach it - it folds `stored + delta`, two sixteen-bit
  values, zero only when both are, and the branch has already established that
  `stored` is not - and `test/test_bpa_rewrite.c` builds the datagram that
  reached it, so the claim is a printed number rather than an argument.
[#222](https://github.com/andrediashexa/hexarouting/issues/222),
[#223](https://github.com/andrediashexa/hexarouting/issues/223).

* **The BGP page's table of what the VTY checks had drifted from the grammar.**
  The page says of it, in as many words, that this table is what `hexash`
  verifies, and then explains that anything not in it is passed through unread
  and comes back marked `<- not checked`. So the table is a claim about the
  grammar, and a reader uses it to predict their own `show this`. It omitted
  `capability` from the neighbour list and `maximum-prefix` and `next-hop-self`
  from the address-family one - three statements the VTY does check, and whose
  absence from the page would have had an operator expecting a marker that
  never appears. `test/test_doc_commands.py` now reads the table and the
  address-family sentence off the page and compares them with `BGP_NEIGHBOUR`
  and `BGP_AF_NEIGHBOUR`, so a statement added to the grammar and nowhere else
  fails. It also asks, of the pool page and the interface page, whether
  every statement those blocks take is **named** somewhere on the page - one
  step earlier than #221 and #224, which were both a thing the box does that an
  operator cannot find. A statement that reaches the grammar and no page is
  that same defect at the moment it is introduced. Deliberately weak: it asks
  whether the page names the statement, not whether it explains it well,
  because the pages are laid out differently and a guard that demanded one
  shape would fail on prose that is perfectly clear.

* **Five wiki pages opened with their own title two, three and four times.**
  The mirror's contract is that each file starts with one `# <title>` line,
  which is the document's title field and not part of its body:
  `tools/wiki-pull.sh` writes it and `tools/wiki-push.sh` strips **that line**
  back off. Push strips one, so a file carrying four publishes a page whose
  body opens with its own title three times over. It is the accumulation
  [#165](https://github.com/andrediashexa/hexarouting/issues/165) describes,
  surviving in the data after the code that caused it was fixed
  ([#225](https://github.com/andrediashexa/hexarouting/issues/225)).

  `test/test_wiki_roundtrip.sh` drove the two scripts against a stub and
  asserted they agree with each other, which is where the defect was; nothing
  looked at the mirror, so the mirror drifted silently. It now reads all 32
  pages and requires each to open with exactly one title. A level-1 heading
  further down stays allowed, because several pages legitimately have them.

  The pages are not published - that needs a token the maintainer holds - so
  this is a repair before anybody read it.

* **Four commands the box has for the moment routing does not come up were in
  the grammar and on no page.** `show ospf neighbor`, `show ospf interface`,
  `show isis adjacency` and `show isis interface` could be found only by
  pressing `?` at the right moment - and those four carry this box's strongest
  claim, the comparison between what the routing daemon holds and what the
  table that actually forwards has. A route that stops half way leaves both
  halves looking healthy on their own, and these are the screens that say so.

  The wiki now has them, including the two diagnoses that are the reason they
  exist: an OSPF adjacency that will not leave `Exchange` is almost always the
  interface MTU, which neither side logs, and an IS-IS circuit that is `Up`
  with zero neighbours is IS-IS frames not being delivered to the system.
  `test/test_doc_commands.py` requires every `show` command to be named
  somewhere in the wiki, read two levels deep and no further: arguments are
  written the way a reader meets them, so demanding the leaf string would go
  red on a page that is right.

* **Two labs matched a `show` line exactly and
  [#141](https://github.com/andrediashexa/hexarouting/issues/141) put a marker
  after it.** `lab/vty.sh` asserts where a statement appears - its block and its
  indentation - by requiring the whole line, and `maximum-paths` is a statement
  this VTY has no grammar for, so it now shows with `  <- not checked` after
  it. A nesting assertion failed over a marker, which is a check measuring the
  wrong thing. The needles stop at the statement now, and the marker is
  asserted once on its own, where it is the point rather than an accident.

  `lab/bgp-policy.sh` had the same shape and its repair was wrong the first
  time: the replacement needle named `set local-preference 120`, which belongs
  to a route-map this lab types at `commit check` and never commits, so it is
  in no running configuration. The suite said so. It names a statement from the
  route-map that IS committed now.

  And the guard added with that repair had a false positive of its own: it read
  the marker out of a COMMENT explaining an assertion and compared prose
  against the product's string. It only reads quoted needles now. A check that
  goes red on work that is right is the worst kind there is.

* **A lab check whose timing the machine could not keep now says so instead of
  reporting a product defect.** `lab/igp.sh` clears an IS-IS adjacency and then
  asserts it is gone. The hello interval is three seconds, so an adjacency that
  is back by then re-formed rather than survived the clear - and on a box
  running eight labs at once a `vtysh` call can take longer than that. It
  reported `clearing it took the adjacency down there and then` as a failure in
  a release suite and passed in isolation, which cost an investigation
  ([#218](https://github.com/andrediashexa/hexarouting/issues/218)).

  `expect_timed` in `lab/verdict.sh` measures the **reading**: on time and
  wrong is a failure, which is that check's whole subject, and late is nothing
  learnt. `LAB NOT MEASURED` is a verdict of its own, the lab exits non-zero,
  and `tools/run-all-labs.sh` counts it in its own column - beside `NO VPP`,
  for the same reason: "nothing found a defect, one check could not be taken"
  has to be readable as that sentence rather than as a failure. It is the shape
  `test_boot_wait.sh` and `lab_no_vpp()` already took.

* **The suite printed "as the header says" without having read the header.**
  An off run's line was `pass (12 check(s) red, as the header says)`, and the
  count was never compared with anything - while the comment directly above
  that code names the hazard it was leaving open: *a lab that used to fail
  eleven of them and now fails one has lost ten assertions without losing its
  verdict*. Its verdict does not change, so nothing else can see it either
  ([#151](https://github.com/andrediashexa/hexarouting/issues/151)).

  Each of the nineteen off-capable labs now declares `# OFF-RED-AT-LEAST: <n>`
  in its own header, and the number is the one that lab's off run measured in
  the release suite of 2026-09-13 rather than one somebody chose. A **floor**
  and not an exact count, deliberately: an exact one has to be edited every
  time a lab legitimately grows, which is a check reporting "you added a test"
  instead of "you broke something", and this repository has already made that
  trade once. Gaining assertions needs no edit; losing them has to be
  deliberate.

* **The RFC 8158 record this box exports was written down in three places and
  nothing compared them.** The UDP template names its elements by IANA name,
  the TCP template lists them by number, and `docs/logging.md` carries the
  table an operator hands their collector. A field added to one and not the
  others is neither a build error nor a test failure: it is two transports
  exporting **different records**, decoded happily by any collector, giving two
  different answers about the same box - and a document that describes neither.

  `test/test_ipfix_record.py` reads all three and requires them to be the same
  list, in the same order, with the same widths, for the block record and for
  the boot marker; and it checks the size comment in `bpa_log.h`, which is a
  fourth statement of the same fact and is arithmetic. The documentation is
  where the name and the number meet, so it is what the two code lists are read
  against - that keeps the comparison at three copies rather than adding a
  fourth. They agree today; the guard is for the day one of them moves.

* **Nine things `commit` refuses, and the page an operator would look at
  carried none of them.** Refusing is the strongest thing this box does: it
  knows the state of the box and the change at the same time, and there are
  cases where applying means losing the box - nobody able to log in, nobody
  able to configure, a log that is on and goes nowhere, routing staged against
  an FRR that is not answering. The commit page described `commit remap` and
  the structure-only caveat and said nothing about any of the rest, so an
  operator who met one had nowhere to look it up.

  The page now carries all nine, quoted **in the words the box prints**,
  because that is the string somebody searches for, with the Portuguese
  explanation and the way out beside each. `test/test_doc_commands.py` reads
  them out of `hexash.py` rather than from a list of its own, so a refusal
  added to the code and to no page fails - and it demands only the runs of at
  least 25 characters that carry no interpolation, because shorter ones are too
  generic to be a useful needle and demanding them would red a page that is
  right.

  The guard found six the first draft of that page had missed or paraphrased,
  which is the check doing its job on the work that introduced it.

* **The box claimed paired address pooling in four places and does not have it
  in every mode.** RFC 4787 REQ-2 is stated as a property this product has in
  `docs/roadmap.md`, `docs/architecture.md`, `src/plugins/bpa/bpa_pool.h` and
  the wiki. It is true unconditionally in deterministic mode, and in dynamic
  mode only until a subscriber fills its block: the extension comes from the
  free list, and under `policy interleaved` the next block index **is** the
  next public address, so it normally lands on a different one - and more often
  as the pool grows, because staying put needs the gap to be a multiple of the
  address count. Measured: an arrival pattern that left 0 of 4 subscribers
  split at four public addresses split 4 of 4 at sixteen
  ([#226](https://github.com/andrediashexa/hexarouting/issues/226)).

  The four statements now say where the property holds and where it stops. The
  mechanism is untouched and the choice between the ways of restoring it is on
  the issue with the cost of each: it changes where every extension in a
  dynamic pool lands, which is the maintainer's to make. What is not a choice
  is the claim - an operator sizes and sells against a conformance statement,
  so one the box cannot honour is worse than the absent property.

  `lab/crossaddr.sh` carries the measurement and is deliberately **out** of the
  suite: its checks assert what the box does today, so it would go red the day
  somebody fixes what it measures, and that is a record rather than a
  regression test.

* **A lab whose dataplane never came up was reported as a product failure.**
  `lab_no_vpp()` asked whether the capture contains any `[ ok ]` at all, and
  answered "something was measured" for a run whose **second line** was
  `connect: Connection refused`. Measured in a release suite:
  `lab/icmp-tcp.sh+workers` reached its verdict with every value empty and
  printed three greens, because an assertion written as `test "${X:-0}" = "0"`
  passes on an empty `X` - each correctly guarded by a precondition that failed
  beside it, which is why the lab still said FAILED. Those greens are not
  evidence that the box answered.

  It reads the **order** now: a green before the first refusal means something
  really was measured and the box then went, which is the half-dead case and
  stays a failure until somebody looks at it. A green after it is a guarded
  assertion meeting an empty value. The boundary is asserted in both
  directions, because moving it the other way would excuse the half-dead run,
  and that is the case this must never take.

* **Nothing compared what `appliance/install.sh` puts on the box with what the
  repository has.** Two directions, and the dangerous one has no symptom at
  all: a drop-in that exists under `appliance/systemd/` and that the installer
  never names is a setting nobody applied, on a box that boots, runs and says
  nothing. That is
  [#172](https://github.com/andrediashexa/hexarouting/issues/172) one step
  earlier - there, a drop-in systemd had parsed and **refused** read
  identically to one that was working - and
  [#182](https://github.com/andrediashexa/hexarouting/issues/182) again, for a
  `linux_cp` pair the product never created.

  `test/test_install_manifest.py` checks both ways, expanding the two `for`
  loops so the nine files they cover are checked rather than skipped. Only
  `appliance/systemd/` is required to be fully installed: the rest of
  `appliance/` legitimately holds things the installer does not copy, and a
  rule demanding everything would be one somebody switches off.

  Everything agrees today. Writing the check found a defect in the check
  itself, which is recorded in it: `install.sh` uses the loop variable `d`
  twice, over the FRR daemons and over the three documents, and resolving it by
  name took the first list for the second and reported four files missing that
  nothing installs.

* **The lab suite's default job count over-subscribed the machine's memory,
  and the way it failed looked like a product defect.** Each lab is a container
  with `--memory=5g` running a VPP that reserves hugepages up front, and the
  default was a flat 16: **80 GB of limits on a 62.8 GB box**. What that
  produced was not a clean refusal - one container's VPP did not start, its lab
  ran every check against a dataplane that was not there, and the suite
  reported eight product failures, one of them a **checksum failure on a
  translated packet**, on the run right after a checksum change landed. The
  same lab alone on an idle box passes eleven checks
  ([#151](https://github.com/andrediashexa/hexarouting/issues/151)).

  The default is now the smaller of the core-based cap and what the memory
  holds, which is 8 on this box - the number its clean runs had been using by
  hand all along. `LAB_JOBS` still overrides it. The arithmetic is asserted at
  8 GB, 62 GB and 512 GB through `HEXA_MEMINFO`, for the same reason
  `HEXA_SELFTEST_ROOT` exists: a machine cannot be made to have 8 GB without
  being a different machine.

  Writing that found a gap in the check itself. Reading `_jobs_by_mem` out of
  the script proves the arithmetic and stays green on a script that computes
  the right number and then ignores it - putting the constant back gave **zero
  red**. There is now an assertion connecting the function to the `JOBS=` line
  that uses it.

* **The boot self-test compared the IPFIX configuration with the dataplane and
  not the pools.** The pattern was already there and well argued - *what the
  box was TOLD at boot, from the file `hexarouting.service` hands to `hexash`,
  against what the dataplane HAS* - and it covered logging only. A pool in the
  saved file that the dataplane refused is applied by nobody and reported by
  nothing: the service succeeds because the rest of the file applied, and the
  line beside it counts the pools that are there rather than the one that is
  missing. Every subscriber behind that pool is untranslated, on a box that
  boots, forwards other people's traffic and answers every command
  ([#172](https://github.com/andrediashexa/hexarouting/issues/172)).

  Names only, deliberately: comparing settings would mean re-deriving the
  dataplane's own normalisation in the self-test, which is the mistake #184
  names - a check that recomputes what it is checking agrees with its own
  arithmetic rather than with the box. A box that has configured no pool is
  told nothing, because an operator who has not made one yet is not a box with
  a defect.

  **uRPF the same way, and more is at stake there.** It is the check that stops
  a spoofed source being translated, and therefore what stops a packet nobody
  sent being attributed to a subscriber in the retention log - a record
  indistinguishable from a true one. A box whose saved configuration turns it
  on and whose dataplane has it nowhere forwards, translates, logs and answers
  everything: there is no counter for a protection that is simply absent. A box
  that never asked for uRPF is told nothing, because most do not run it.

  **And the MTU, which is the mismatch this project has already paid for
  twice.** An interface whose saved `mtu` the dataplane did not take still
  forwards; what happens next is that an OSPF adjacency will not leave
  `Exchange`, because the two ends advertise different MTUs and **neither end
  logs anything** - the fault `docs/routing.md` calls Pitfall 1, and the one
  that costs an afternoon rather than a minute. Until now the only thing that
  noticed was a finding at commit time, which says nothing on a box that was
  rebooted. The failure names both numbers, and an interface the box does not
  have is left to the pair check that is about exactly that.

  **And the addresses, which is the one that can leave the box unreachable.**
  An address in the saved file that the dataplane did not take is not a
  degraded box: it answers on one fewer address than whoever configured it
  believes, and if that was the management address nobody can reach it to find
  out. The check reads the block `show interface addr` prints for that
  interface and not the whole output, because an address that landed on
  another port is still missing from this one - a management address answering
  on the subscriber-facing interface would otherwise read as correct.

  57 checks on master before this run of work, **141** after.

* **The BPA nodes' ordering behind shallow virtual reassembly had nothing
  behind it.** `docs/architecture.md:1879` states that the BPA feature nodes
  MUST declare `runs_after` on `ip4-sv-reassembly-feature`, because a node
  placed ahead of reassembly reads `vnet_buffer(b)->ip.reass` before it has
  been written: the following fragment is matched on a garbage port, opens a
  second session and burns one port of the subscriber's block per fragment.
  All four declarations could be deleted with every suite and every lab green
  ([#239](https://github.com/andrediashexa/hexarouting/issues/239)).

  **The box cannot see the deletion, and that was measured rather than
  assumed.** With the four declarations gone, VPP 26.06 still computes
  `ip4-sv-reassembly-feature` then `bpa-in2out` on the arc, because its sort
  falls back to registration order when no constraint names the pair and
  registration order happens to agree today. Every packet assertion in
  `lab/frag.sh` stays green, which is why the lab never caught this: the
  deletion removes the guarantee, not the current behaviour. So the missing
  declaration is caught over the source, by `test/test_feature_order.py` in
  `make test` - four failures, one per node, and a fifth node added without
  the declaration is a failure of its own.

  **`lab/frag.sh` now asserts the arc the box actually computed**, which is
  the half the source cannot make: every BPA node on `ip4-unicast` must appear
  after `ip4-sv-reassembly-feature` on pg0, pg1 and pg2. A declaration that is
  present and ineffective - a VPP upgrade renaming the reassembly feature,
  another plugin's constraints reshuffling the arc - is red there and green in
  `make test`. Shown failing by inverting the constraint to `runs_before`: the
  arc comes out `bpa-in2out ip4-sv-reassembly-feature`, the three ordering
  checks go red, and so do the six fragment assertions, with **two** sessions
  opened by the two fragments instead of one. That is the defect itself, on
  the wire.

### Added

* **A statement this VTY carried no longer looks like one it checked.** Inside
  `router bgp 65000`, `neighbor 10.0.0.2 remote-as 65001` is walked by the
  command tree while `coalesce-time 1000` is offered by `?` nowhere and reaches
  FRR unread - and the two printed identically, so an operator reading a
  configuration could not tell what the box had verified from what it had
  merely forwarded, and read a guarantee into both. Every routing statement the
  command tree cannot read is now marked `  <- not checked` in
  `show configuration`, `show running-config` and `show this`, and a two-line
  note at the end says what the mark means and how many there were.

  What decides it is **the command tree itself** - the same walk that decides
  what `?` offers in that block - and never a list of modelled statements kept
  beside it: a second list has to be kept in step by hand, it goes stale, and a
  stale list says a statement was checked when nothing checked it. So the same
  statement is marked inside `router bgp` and not inside
  `address-family ipv4 unicast`, because the two blocks have different
  grammars, and teaching the tree a statement is the whole change needed to
  stop it being marked. There is an assertion for that property specifically.

  `show this` gets it too, and that is the one that matters most: it is what an
  operator runs while they are still typing. A block this VTY has no grammar
  for at all carries the mark **on its header**, because "nothing in here was
  read" is different news from "this one statement was not".

  The mark is sixteen characters because the console this box is reached on
  when the network is down is 80 columns and the statements most likely to be
  marked are the long ones; the marker it replaces put a realistic IPv6
  neighbour statement at 89 columns and wrapped it. The closing note is
  printed **only when something is marked** - passthrough is the normal case
  for FRR configuration, and #205 and #208 are the standing precedent that an
  alarm which is always on is one nobody reads.

  `commit` says nothing about it, by that same rule, and `commit check` now
  names the count: it is a question the operator chose to ask whose whole
  subject is how much was verified, and it already answered with "structure
  only", a sentence that names no number
  ([#141](https://github.com/andrediashexa/hexarouting/issues/141)).

* **The marking is presentation only, asserted separately.** `save` writes a
  file the box reads back at boot and `commit` hands the same statements to
  `frr-reload`; a mark that leaked into either becomes a statement FRR does not
  understand, the load is refused whole, and the box comes up with **no
  configuration at all** - which is how both boot defects behind #182 ended.
  After a `show configuration`, a `show running-config`, a `show this` and a
  `commit check`, `Config.text()` is byte for byte what it was, the list handed
  to `frr-reload` is the statements as typed, and the saved file carries
  neither the mark nor the note and parses back to the same statements.
  `Config.parse` strips both, because an operator copies what is on their
  screen into a file.

* **OSPFv3 is a protocol this box runs, rather than one it offered and could
  not do.** `router ospf6` was a word the VTY did not model: it went to FRR
  untouched, committed, and `ospf6d` was enabled in no file the product shipped
  - not `appliance/install.sh`, not the boot self-test. The statement was
  reachable only by typing it blind, since `?` never listed it and TAB never
  completed it, and `lab/igp.sh` asserted the refusal that followed as though a
  protocol the box refuses were a finished feature.

  The issue asked for a decision - take the statement out of the command tree,
  or make it work - and it was settled by measuring. With an OSPFv3 packet
  injected at a `linux_cp` pair and tcpdump on the TAP, IPv6 protocol 89 to
  `ff02::5` **arrives**: VPP has no local handler for protocol 89, so it falls
  through `ip6-local` to `ip6-punt` and linux_cp's per-pair punt redirect hands
  it out of the TAP, which is the same reason OSPFv2 has always worked. The
  adjacency then reaches `Full` and the prefix reaches the VPP FIB, with no
  punt beyond the one OSPFv2 uses. So the transport was never the problem and
  the daemon list was.

  `ospf6d` is enabled by the installer and checked by the boot self-test beside
  the other three; `router ospf6` and `ipv6 ospf6 …` are in the command tree
  with grammars measured against a live FRR 10.7.1 rather than written from
  memory; and `lab/igp.sh` configures OSPFv3 at this box's own VTY and asserts
  the prefix in the VPP FIB, the adjacency at `Full`, and the withdrawal when
  the far end drops it.

* **The inherited-MTU stall is reported on an OSPFv3 interface too.** RFC 5340
  keeps the Interface MTU field in the database description packet, so a pair
  whose TAP is at 9000 against a 1500 wire stops an OSPFv3 adjacency short of
  `Full` exactly as it does an OSPFv2 one - measured, and measured again
  reaching `Full` within seconds of the two being made to agree. Every check in
  `ospf_findings` keyed on `ospf_state()`, which reads nothing out of an OSPFv3
  configuration, so the box had said nothing. `ipv6 ospf6 mtu-ignore` and
  `ipv6 ospf6 passive` silence it, the way the `ip ospf` spellings do.

  It is the only OSPFv2 finding that carries over, and the rest stay **silent**
  on an OSPFv3 stanza rather than firing something that cannot be right: they
  are about `network … area`, `passive-interface` and an interface inside an
  IPv4 CGNAT pool, none of which OSPFv3 has. #205 and #208 are the standing
  precedent that an alarm which is always on is one nobody reads.

* **The control plane's share of the CPUs, reported on the console.** The
  tuner gives the OS the dataplane node's first physical core and isolates the
  rest, so on a 32-core socket FRR, `frr-reload`, `vtysh`, `hexash`, `sshd`
  **and** VPP's main thread share one core - and the main thread takes the
  worker barrier once a second. The self-test now says how many cpus are left
  and warns when that is one **while more than one core is isolated**, which is
  the arithmetic statement that cores could have been handed back and were not;
  a box with one isolated core has nothing to give back and is not warned. The
  remedy named is `HEXA_MAX_WORKERS`, which the product already ships
  ([#172](https://github.com/andrediashexa/hexarouting/issues/172)).
* **Measured and written down: raising `HEXA_MAX_WORKERS` costs a boot with no
  dataplane, and lowering it does not.** `tuned_now` requires the cores a
  profile pins to be cores the kernel is **already** isolating, so lifting a
  cap asks for cores that are not isolated until the command line changes on
  the next boot, and the box spends one boot in FIRST BOOT MODE: no workers,
  no hugepages, DPDK disabled. Lowering a cap takes effect on the very next
  boot and the freed cores leave `isolcpus` on the one after. That is the
  tuner refusing to pin a worker to a core the kernel has not isolated, which
  is right - but it is a maintenance window, and it was written down nowhere.

* **`lab/install-iso.sh` reads the memory bounds back off systemd**, both
  halves: `MemoryMax=512M` on `hexarouting.service` **and** no bound on
  `frr.service`. A unit systemd parsed and refused reads exactly like one that
  took, and this is the only place in the repository with a real systemd. The
  absence is as deliberate as the presence - a `MemoryMax` that appeared on
  `frr.service` by accident is a five-second restart loop on the box with the
  most to lose.

* **A third state for a check whose timing the machine could not keep:
  `NOT MEASURED`.** `test_boot_wait.sh` measures the STIMULUS, not the run: a
  dataplane that answered on time and was announced anyway is a `FAIL`, which
  is that check's whole subject, while one that answered a margin late says
  nothing about the quiet period. It is the shape `lab_no_vpp()` already took
  in the lab suite, and it is not a pass: the run still exits non-zero and says
  it proved nothing, so a quiet period that shrank cannot reach green by
  waiting for a busy machine.

* **`test/test_doc_commands.py`: every command the documentation tells a reader
  to type, typed at the command tree.** The wiki is where this product promises
  things to somebody who does not have the source, and nothing checked that
  what it shows exists. It reads the prompt lines inside fenced blocks, puts a
  shell in the mode and the block the prompt says the reader is in, and walks
  each command as far as the tree enumerates - a command that is not there and
  a sub-command that is not there both fail; arguments are not checked, which
  is the same boundary `?` and TAB have at the real VTY. It found both of the
  defects above. `install` is the one deliberate exception, and the exception
  requires the page to explain itself, so it cannot rot into a hole.

  It reads `docs/` and `README.md` beside the wiki, and `docs/` was not the
  softer half: `docs/routing.md` showed `hexarouting# show cgnat ?`, and a
  four-line transcript that entered an interface and then typed `mtu 1500` at
  the `[edit]` prompt, which is the top of configuration mode, where `mtu` is
  not a statement at all. Both are fixed.

* **The connection records can be kept on two disks, so one disk failing does
  not lose the year the Marco Civil requires**
  ([#83](https://github.com/andrediashexa/hexarouting/issues/83)). In dynamic
  mode the allocation log **is** that record. It has had a filesystem of its
  own since 0.30.0, so a full log cannot stop the box working; what it did not
  have was a second copy, and losing it is not the same class of loss as
  downtime - downtime ends with a reinstall and this does not end at all.

  The installer now offers a second disk, mirrors **the records onto it and
  not the system**, and says why: a dead system disk is a reinstall, and the
  records on the survivor are read back on another machine - which
  `lab/install-iso.sh mirror` does, off the leg alone, with no array
  assembled, and with that box switched off. The measured cost of the choice
  is in the same lab: the survivor booted on its own reaches no bootloader at
  all and sits at the firmware.

  **A box with one disk is untouched.** It is never asked, it gets no array,
  and its self-test says nothing about mirrors - asserted on an installed box
  rather than assumed. The default is `none` and a bare Enter takes it: a
  second disk in a machine is not consent to destroy what is on it, and the
  confirmation names both disks, because a confirmation that repeats one disk
  back while a second is destroyed behind it is not a confirmation.

  `mdadm --create` exits **zero** having built a degraded array, so the
  install reads `/proc/mdstat` back before it reports success. And the boot
  self-test states the mirror on every boot: **degraded is a failed check**,
  because the volume stays mounted and the records keep being written while
  the box is one failure from losing them; a rebuild in progress is a warning
  with its percentage, since reporting a repair as a failure teaches whoever
  reads that console to skip the line.

  Unattended installs answer it with `hexarouting.mirror=<disk>` or a `mirror`
  line in the answers file. The medium now carries `mdadm`, and the build
  refuses an image without it.

  **Three defects this turned up, all of them in the box around the mirror
  rather than in the mirror.** Each was found by booting a machine, and each
  one named the next:

  * **A degraded array does not start on its own, so the records volume never
    mounted.** With the mirror disk detached the box came up perfectly and
    `/proc/mdstat` read `md0 : inactive vda3[0](S)` - udev claims the surviving
    leg and stops, because one leg of two is not a complete array, and an
    inactive array has no device to mount. `nofail` did its job and
    `/var/log/hexarouting` was back on the root filesystem: the arrangement the
    separate volume replaced, on a box that had already spent a disk. The
    installed system now carries a unit that runs the array with whatever disks
    are left, before `local-fs.target`.
  * **A box that already had a mirror could not be reinstalled.** The medium's
    udev assembles the array it finds, and `sfdisk` will not rewrite a
    partition a running array is holding. The arrays on the disks about to be
    erased are stopped first.
  * **And stopping them was not enough**, because an md superblock lives 4 KB
    *into* the partition: rewriting the table made udev re-probe and assemble
    the array again, between the partitioning and the `mkfs`. The superblocks
    are erased with the stop - and per disk, immediately before each write,
    because doing it once for both left the second disk exposed for as long as
    the first `sfdisk` took.
  * **And the erase brought back what it had removed.** Closing a writable file
    descriptor on a whole disk makes the kernel re-read its partition table,
    which is what `mdadm` does while looking for a superblock to erase - so
    udev saw the partition appear and assembled the stale leg on it, `inactive`,
    holding the disk the erase had just freed. The release now repeats until a
    pass finds nothing left, and prints `/proc/mdstat` if it never does. That
    last one was only readable because the step before it had been made to say
    what was in the way instead of leaving it to be deduced from `sfdisk`.

  The first of those is the one worth reading twice: everything about the
  mirror was correct, and the box would have spent a year writing records to
  the disk that was left, under a self-test saying so on every boot and nobody
  necessarily reading it.

### Fixed

* **The pool-announce warning went silent on the ordinary edge box, and had no
  unit assertion at all.** The check that says a pool's public prefix is
  advertised by nothing
  ([#145](https://github.com/andrediashexa/hexarouting/issues/145)) treated any
  line beginning `redistribute`, anywhere in the configuration, as something it
  could not see through. Measured on FRR 10.7.1, with a covering static route
  present and BGP configured throughout:

  ```
  router bgp 65000 / address-family ipv4 unicast / redistribute static
    *>  200.150.9.16/28  0.0.0.0  0  32768 ?
  router ospf / redistribute static        (BGP carrying no redistribute)
    No BGP prefixes displayed, 0 exist
  ```

  The two statements are the same six characters and only one of them
  announces anything. So an edge box that leaks BGP into its IGP - which is
  what `ospf_findings` already warns about - exempted itself from this check
  entirely and got no warning on a pool nothing announced.

  The block context now comes from `config.block_paths()`, the one thing here
  that reads both shapes `routing` arrives in: indented when FRR read it back,
  flat when it was typed at this VTY. A `redistribute static` under `router
  bgp` is also **resolved** against the candidate's own static routes rather
  than treated as unfollowable, because FRR's static routes are all in the
  configuration - so originating an aggregate from a blackhole route, which is
  the ordinary way an ISP does it, silences the warning, and a static route
  that covers something else does not. `aggregate-address` and `network …
  route-map` blind it, both measured.

  **It stays a warning**, and the reason is now measured rather than argued
  from the issue's wording. `network 200.150.9.16/28` with no matching route in
  the RIB is stored by FRR and appears in `show bgp ipv4 unicast` **without
  `*>`** - not valid, not best, advertised to nobody - and becomes `*>` the
  moment `ip route 200.150.9.16/28 blackhole` exists. What this check can test
  is therefore a proxy for the announcement and not the announcement, and a
  commit MUST NOT be refused on a proxy: it would block configurations that
  work while still passing the box whose `network` statement originates
  nothing. That is the shape of
  [#208](https://github.com/andrediashexa/hexarouting/issues/208), where
  refusing a whole commit over a BGP statement was itself the defect. The
  advice now names the blackhole route beside the `network`, because advice
  that does not work when followed is worse than no advice.

  The check was inline in `commit` and so could only be reached by running the
  lab. It is `pool_announce_findings()` over `bgp_originations()` now, in the
  family of `ospf_findings` and `bgp_errors`, with unit assertions in
  `test/test_hexash_complete.py`; fourteen mutations of the product were each
  run to red before the work was called done, and one of them found an
  assertion that was green against the defect it claimed to guard.

* **An indented `!` put every statement after it in the wrong block.**
  `config.block_paths()` read any `!` as FRR's top-level stanza separator and
  cleared the block stack. FRR 10.7.1 also writes an **indented** one inside a
  stanza, captured from a live daemon:

  ```
  router bgp 65000
   !
   address-family ipv4 unicast
    network 200.150.9.16/28
   exit-address-family
  exit
  ```

  So the `network` statement belonged to no block, and anything asking "is
  this line under `router bgp`" answered no for a box that had typed it
  exactly there. `cfg.routing` is read back from FRR, so that is the shape of
  **every commit after the first one** - it was the pool-announce check above
  that found it, warning about a pool the box was announcing correctly. Only a
  `!` at column 0 returns to the top now.

  It also **moved a route between tables**. A `vrf red` stanza whose static
  route follows an indented `!` had that route hoisted into the global `router
  static` block, so the file said the route was global and the operator had
  put it in a VRF - which is exactly the defect
  [#180](https://github.com/andrediashexa/hexarouting/issues/180) fixed,
  reintroduced through a line nobody types. The assertion that existed for
  #180 had no `!` in it and was green against this the whole time; the new one
  is written against what a live daemon printed.

  And it **shortened `show this`**. Asked what `router bgp 65000` holds, on a
  configuration read back from FRR rather than typed, the box left out the
  address-families and everything in them - because FRR writes ` !` between a
  router's own statements and its first family, and past that separator the
  family sat at no path at all. `frr_block_lines()` promises the opposite in
  its docstring: an operator standing in a router is asking what that router
  holds, and its address-families are part of the answer. Not a wrong answer
  they could see was wrong, a **shorter** one, with nothing saying anything was
  missing. `lab/vty.sh` asserts the family is there but does it mid-edit, where
  the candidate is flat and carries no `!`, so it was green throughout; its
  after-commit case reads `router static`, which is top-level and cannot show
  this. The assertion for it is a unit one, on the read-back shape.

* **`no <statement>` inside a pool block was accepted in silence and did
  nothing.** `no block-size 512`, `no policy interleaved`,
  `no mode deterministic`, `no ports 4096`, `no vrf 7`: each printed nothing,
  removed nothing, and `show this` went on showing the setting. The operator
  walked away believing it was gone. The `no` branch of the pool handler was an
  if/elif chain with no `else` - twelve statements had a negation handler and
  every other `no` fell out of the bottom of it - while an unknown word in the
  same block was refused correctly, so the machinery for refusing was there and
  this path went round it
  ([#224](https://github.com/andrediashexa/hexarouting/issues/224)).

  Every `no` now either does the thing or says why not, and **which of the two
  is read off the model, not chosen**: a pool statement can be negated exactly
  when `Pool.lines()` is able to leave it out. `block-size`, `policy` and
  `mode` are written unconditionally - a pool has one whatever the
  configuration says - so `no block-size 512` has nothing to mean, and removing
  the line would only stop the configuration saying what the pool is doing
  while the pool went on doing it. Those three are refused, and the refusal
  names the value to set instead. `ports`, `vrf` and `outside-vrf` go the other
  way and are **newly removable**: each is written only when set, "" is a state
  the model already has and already means something distinct - the device's own
  port range, and "every VRF", which the model says out loud is not the same
  answer as VRF 0 - and that state had become unreachable, because once `vrf 7`
  was typed there was no line that took it back. There is an assertion that
  walks every pool statement and holds the VTY's answer against the model's, so
  a statement added later cannot fall into the same hole.

  The user block was measured too, and it is a smaller fault than the issue
  reported: `no role`, `no password` and `no ssh-key` all did what they say. It
  is the refusal that was wrong - `no wibble` answered "unknown statement in
  user mode: **no**", naming the one word in the line that is not the problem,
  and sending an operator off to find out whether the block has a `no` at all
  when it has three of them. It names the word after `no` now.

* **`no <dataplane statement>` inside an interface was handed to FRR.**
  `no mac-address aa:bb:cc:dd:ee:ff` fell through to the routing passthrough
  and was staged for FRR, which has no opinion about the MAC of a VPP
  interface: at commit it is a statement FRR does not know, arriving in the
  middle of applying. `Config.parse` had always understood that line - only the
  VTY did not - so the same statement meant two different things depending on
  whether it was typed or loaded from a file
  ([#224](https://github.com/andrediashexa/hexarouting/issues/224)).

  `no mac-address` now does what `no mtu` does: the candidate stops naming a
  MAC for the interface, so the commit sets none and the interface keeps the
  one it has. Which of an interface's statements are this box's and which are
  FRR's is now one table that **both directions read** - the branch that
  intercepts `mtu 9000` and the branch that refuses a `no` with no handler -
  because a set written out beside the handlers would go stale the next time a
  dataplane statement was added, and that statement's `no` would go straight
  back to leaking into FRR. `no vlan` is refused rather than handled: a
  sub-interface is named after its tag, so one without a tag is not something
  the dataplane can hold, and the refusal says to remove the sub-interface
  instead. `no` on a statement FRR owns - `no ip ospf cost 10` - still goes to
  FRR, because that is FRR's to remove.

* **`?` offered `no` in no block at all**, so the only way to reach any of the
  negations above was to type them blind
  ([#224](https://github.com/andrediashexa/hexarouting/issues/224)). A pool, a
  user and an interface now offer `no`, and what follows it is **derived from
  what can actually be negated** - the pool's from its negation handlers, the
  interface's from the block's own statements minus the ones it refuses, the
  user's from its statements - never from a hand-written second list. An
  assertion types every word the completer offers after `no` into the block and
  fails if the block refuses it.

  `no` on its own is refused too, wherever it is typed. It was staged for FRR
  as the bare word, which FRR answers with an unknown command in the middle of
  applying - and `no` alone is not a command anywhere, so there was nothing it
  could have meant.

  One measured consequence, said out loud: putting `no` into the interface
  grammar means #141's `<- not checked` marker can now walk it, so
  `no ip ospf cost 10` is no longer marked - which is the marker agreeing with
  itself, since the positive `ip ospf cost 10` was never marked and both are
  forwarded to FRR. `no ip ospf wibble 3` is still marked, and so is anything
  else the tree cannot read; the marker follows the grammar, and the grammar
  grew.

* **A received Router Advertisement never reached the Linux side of a
  `linux_cp` pair, so BGP unnumbered never came up.** The VTY takes
  `neighbor vppwan9 interface remote-as external`, FRR holds it, and the peer
  stood in `show bgp summary` at Idle with "remote AS 0" for ever, with the
  configuration entirely correct. FRR learns an unnumbered peer's address from
  that advertisement and from nothing else
  ([#213](https://github.com/andrediashexa/hexarouting/issues/213)).

  It was filed as an IPv6 multicast punt gap and it is not one. IPv6 multicast
  reaches the TAP: an OSPFv3 packet to `ff02::5` arrives on a pair with no
  address on either end, measured, because VPP has no local handler for
  protocol 89 and the packet falls through `ip6-punt`, where linux_cp's
  per-pair redirect DVRs it out of the TAP. That is also why OSPFv2's
  `224.0.0.5` has always worked. ICMPv6 is different: VPP handles it itself,
  so the advertisement reached `ip6-icmp-input` and from there the node
  registered for type 134, `icmp6-router-advertisement`, which is declared
  with exactly one next node - `ip6-drop` - and no configuration adds another.
  Its siblings punt: `icmp6-neighbor-advertisement` is declared with
  `ip6-punt`, which is why neighbour advertisements have always reached Linux.

  With no knob for it, the fix is a node. `src/plugins/bpa/bpa_ra.c` registers
  ICMPv6 type 134 to a node of its own whose only next is `ip6-punt`, after
  `ip6_ra_init` so the registration is not overwritten, and counts what it
  punts. What is given up is VPP's own processing of a received advertisement,
  which is the RS retransmit timer and the `ip6_ra_report` subscribers - the
  DHCPv6-PD client's, not this product's. On a box where every dataplane port
  is paired into the kernel, the kernel is the IPv6 control plane, as it
  already is for ARP.

  Punting them hands a neighbour a lever it did not have while VPP was
  dropping them, and the kernel's own default takes it: measured in a
  container, the peer's advertisement put `default via fe80::... proto ra` in
  the kernel's table, `linux_nl` synced it, and `show ip6 fib ::/0` came back
  forwarding through the neighbour. `appliance/etc/sysctl.conf` now carries
  `net.ipv6.conf.{all,default}.accept_ra = 0`, which costs the unnumbered
  session nothing: zebra reads advertisements on a socket of its own rather
  than through the kernel's autoconfiguration.

  `lab/routing.sh` asserted only that the far end reached Active, which was
  the half of the exchange that did cross the pair. It now asserts the session
  reaches **Established** on both sides, over a link-local address neither box
  was told, with nothing typed that an operator would not type: no `ip mroute`
  and no punt line in the lab, which is the trap
  [#168](https://github.com/andrediashexa/hexarouting/issues/168),
  [#182](https://github.com/andrediashexa/hexarouting/issues/182) and
  [#215](https://github.com/andrediashexa/hexarouting/issues/215) each cost.

* **A box that was stopped left every block it held open in the retention
  log, and a box that crashed could not be told from one still running.**
  Nothing in the plugin ran on the way out and `vpp.service` had no
  `ExecStop`, so a planned reboot or an upgrade - which is every box, not the
  rare pool delete of
  [#215](https://github.com/andrediashexa/hexarouting/issues/215) - left one
  open allocation per block held, for as long as the log is kept. On the way
  back up it got worse: the pin replay emitted a second `natEvent 16` for each
  pinned block with no 17 between them
  ([#217](https://github.com/andrediashexa/hexarouting/issues/217)).

  The drain is in two places, and which one was measured rather than chosen.
  VPP's UDP IPFIX export is not a kernel socket: it is a packet the dataplane
  sends, handed to `ip4-lookup` as a frame, and the graph stops before any
  in-process exit hook is reached. Measured with one block held at the stop
  and a real collector on the wire, a `VLIB_MAIN_LOOP_EXIT` function runs on
  SIGTERM and finds the block, but over UDP the record is written and
  swallowed; over TCP it arrives, because that exporter owns a kernel socket.
  So the plugin keeps the exit hook - it covers every graceful exit and it is
  the only thing that covers them for TCP - and a `vpp.service` drop-in runs
  `vppctl bpa stop-drain` from `ExecStop`, before the signal, while the graph
  is still there to carry the UDP records. The two do not double up: the drain
  latches and the hook sees the latch. `bpa stop-drain` is safe to type by
  hand as well: a box that drains and then does not stop re-announces every
  block it still holds a few seconds later.

* **A boot marker, so a collector can read an allocation that has no release.**
  A power loss emits nothing and never can, so the drain alone would have
  looked like a fix and left the log ambiguous on exactly the boxes that
  crashed. The box now announces, on a second template on the same stream,
  when this incarnation of the dataplane started - IE 160,
  `systemInitTimeMilliseconds`, which IANA registers as the timestamp of the
  last (re-)initialization of the IPFIX device. An open allocation followed by
  a **new** init value ended at that restart; one with no new value is still
  in force. **The shape is a proposal**, not a settled interface: RFC 8158 has
  no event for this, an Options Template scoped by `observationDomainId` would
  be formally better and VPP's ipfix-export cannot build one, and
  `docs/logging.md` carries the argument and the three shapes that were not
  used.

* **`test/test_selftest.sh` had a duplicated verdict block, so its last third
  had never run.** A second `exit 0` copy of the pass/fail block sat two thirds
  of the way down the file, left behind when a section was inserted above it,
  and everything below - twelve assertions, the `linux_cp` pair checks of #182
  among them - printed ALL TESTS PASSED without executing. Found because a new
  assertion added at the end of that file produced no output at all. A test
  that exits before its assertions is the same defect class as a lab that
  crashes before reaching one, and it is invisible for the same reason.

* **A dynamic pool's capacity line understated what the box can serve.** The
  caveat added for [#210](https://github.com/andrediashexa/hexarouting/issues/210)
  divided the usable figure by the worker count, and that is only the smallest
  block slice when there are at least as many inside addresses as blocks. A
  pool with **more blocks than inside addresses** loses nothing to the
  sharding, because no worker can be handed more subscribers than it has
  blocks for. Measured on 12 blocks in front of 8 inside addresses: the box
  printed 4 at two workers and 2 at three, while all 8 were served at one, two
  and three. Understating is still a number an operator sizes wrongly - it just
  sends them to buy addresses they already own.

  The figure is now the largest *n* for which **every** set of *n* subscribers
  is served: the smallest slice among the workers that can be crowded, and the
  whole inside set when none of them can be. **A worst case and deliberately
  not a mean** - the operator has no say over which of their addresses are
  switched on at any moment, and a figure that holds on the average leaves a
  box refusing subscribers below the number it printed, which is the same false
  promise arriving one step quieter. It is printed only when it is below the
  ceiling, because a caveat that repeats the number above it is one people
  learn to skip.

  `lab/dynamic.sh` now asks the box which thread holds the thinnest slice and
  which thread each candidate lands on, then sends the claim's worth of
  subscribers **crowded onto that slice**. Neither the sharding rule nor the
  subscriber hash is recomputed in the lab: a lab that recomputes them agrees
  with itself instead of with the product. `dynamic` is in `BOTH_WAYS` now,
  which it was not - its single-threaded run cannot see this defect at all,
  because with one slice the partition and the hash agree by accident.

  The mechanism itself is unchanged and remains a decision for the maintainer,
  with the evidence now on the issue: a shared subscriber-to-worker table costs
  **16 to 21 clocks** against `bpa-in2out`'s own cost, which was 396 when that
  reading was taken and is 346 since
  [#63](https://github.com/andrediashexa/hexarouting/issues/63) in this same
  release - so the read is a larger share than it was and still not the
  expensive part; the mutability is; and letting a worker borrow a block from
  its neighbour, implemented and measured, **loses every reply**, because the
  return path resolves the worker from the block index by arithmetic.

* **The boot self-test reported the cores the dataplane was ASKED for and
  never the cores it GOT.** `full tuning active: corelist-workers 1-7` has been
  on the console since the tuner existed, and it prints
  `/etc/vpp/startup.conf`. Nothing on an installed box ever read whether the
  running dataplane held those cores, or whether the kernel was isolating them.
  Both come apart without a symptom: a VPP that has been running since before
  the profile was rewritten keeps the cores it started with, and a kernel
  command line that did not get updated leaves a poll-mode worker sharing its
  core with the scheduler. In each case the box boots, forwards, answers every
  command and delivers a fraction of its capacity, with the only outward sign
  being a thread that reads 100% busy either way. The self-test now compares
  three places and none of them is the one that made the decision:
  `/etc/vpp/startup.conf`, `/proc/<vpp pid>/task/*/status` and
  `/sys/devices/system/cpu/isolated`. A worker on a core the kernel is not
  isolating, and a dataplane holding cores its own profile does not name, are
  both failed checks that name the core and the command to fix it
  ([#172](https://github.com/andrediashexa/hexarouting/issues/172)).
* **`[ ok ] rx queues:` with nothing after it.** The `||` sat after `head -1`,
  which succeeds on empty input, so a box whose profile names no
  `num-rx-queues` printed the label and no value - an assertion that cannot
  fail, in the reporting half. Seen on an installed box. `test/test_selftest.sh` now holds it against a fake box whose profile names no `num-rx-queues`, which is the common case rather than an odd one: the old form fails it with `the rx-queues line printed a label and no value at all`.

* **`wait-for-vpp` counted polls and printed them as seconds, so the boot
  banner fell a second early and a timeout gave up a second short.** `n` was
  incremented before the `sleep 1`, which made it one larger than the time that
  had actually passed. Measured directly, with a dataplane that never answers:
  the quiet period of three ended at **2007 ms** and a thirty second timeout
  gave up at **29069 ms** while printing "within 30s". With the increment after
  the sleep: **3009 ms** and **30073 ms**
  ([#218](https://github.com/andrediashexa/hexarouting/issues/218)).

  That missing second was the whole of the intermittent failure. `make test`
  fails about one run in three under a lab suite, and the check that lost was
  `test_boot_wait.sh`'s "a SHORT wait is still silent": it stimulates a two
  second wait, the banner fell at 2.009 s against a stimulus at 2.000 s, and
  which of the two won was decided by how long a `fork` took. Under sixteen
  containers running `apt-get` the fork loses. Pure CPU saturation never
  reproduced it, because that slows the poll loop too.

* **A killed test suite and a failing one were the same thing from outside.**
  The `make test` recipe ran each suite and read its exit status, so a suite
  that was interrupted, crashed or never started reported like one that had
  been read. `tools/run-tests.sh` reads the verdict line instead and reports
  four states: `pass`, `FAIL`, `NOT MEASURED` and `NO VERDICT`, numbers the
  suites `[13/25]`, and names where a stopped suite stopped. The seven C
  suites and `test_hexash_confirm.py` had five spellings of a verdict between
  them and two printed none at all; they now share `bpa_test_verdict()`.

* **`version-stamp.sh` added a line to the account's `~/.gitconfig` on every
  run.** It marks the repository as a safe directory so git will answer inside
  the build container, and it did that with `--add` unconditionally. The script
  runs on every build, every release gate, every lab that stamps an image and
  every `make test`, so the entry landed once per run for ever, in a file that
  belongs to whoever runs the build rather than to this product. Measured on
  the machine it was found on: **2325 copies of the same entry** in a
  `~/.gitconfig` with four other lines in it, and 552 of the distinct paths
  were `/tmp` clones that no longer exist. git parses the whole configuration
  on every invocation in every repository, and an allowed-directory list nobody
  can read is one nobody audits. It is now added only when
  `git config --get-all` does not already have it, and
  `test/test_version_stamp.sh` holds that: six runs must leave the file the
  size the first one made it. With the `--add` put back it fails with
  `the configuration grew from 2 line(s) to 7 over five more runs`
  ([#220](https://github.com/andrediashexa/hexarouting/issues/220)).

* **`show this` worked inside a pool, a user and an SNMP user block and `?`
  listed it in none of them.** Inside an `interface`, `router bgp` or `rpki`
  block it did, because the line that adds it sits in the branch for FRR
  blocks and this product's own blocks are modes rather than context entries.
  The comment beside that line said "every block" while the code covered half
  of them. `show this` is the only way to read a block back before committing,
  and the block where an operator needs it most is the pool, which is this
  product's whole subject and the example the wiki uses
  ([#221](https://github.com/andrediashexa/hexarouting/issues/221)).

  The word alone was not the whole of it: `show ?` inside a block offered
  nothing, in **every** block including the FRR ones, so `this` could still
  only be reached by typing it blind. Every sub-command of `show` answers from
  inside a block - measured, not assumed - so the whole tree is offered there
  now.

* **Two wiki pages showed `hexarouting# configure`.** `configure` is an
  operational command and `#` is the configuration-mode prompt, so that is a
  transcript no box produces: at `#` the reader is already in configuration
  mode, where `configure` is not in the tree at all.
* **Every IPFIX record the TCP exporter still held was lost when the box
  stopped, and the ones lost were the releases.** The socket is non-blocking
  and the spill to disk only triggers above 1 MB, so between "the collector
  stopped reading" and "1 MB has piled up" the records live in one place: a
  buffer inside the VPP process. The stop drain writes one `natEvent 17` per
  block still held into that same buffer, and then the process leaves. In
  dynamic mode a release that never arrives leaves the allocation open in the
  retention log for ever, which is
  [#217](https://github.com/andrediashexa/hexarouting/issues/217) reopened by
  the transport ([#219](https://github.com/andrediashexa/hexarouting/issues/219)).

  Measured on the build before the fix, with 1501 blocks held: 15372 bytes in
  the buffer, the drain reporting `released 1501 block(s) still held`, 0 bytes
  on disk after the stop, and the UDP control delivering all 3012 records while
  TCP delivered **none** of them.

  **It is not only a stalled collector.** With the collector reading as fast as
  the box writes and nothing wrong anywhere, the same measurement found 0 bytes
  pending before the stop and **70020 bytes still in the buffer at the exit**,
  because the drain writes every record in one go and services the socket once.
  A box with enough subscribers reaches this on an ordinary reboot.

  The fix is a flush at `VLIB_MAIN_LOOP_EXIT`: whatever the socket refuses goes
  to the spill file and is replayed in order on the next connection. The bound
  is **one `write()`** and there is deliberately no timed wait, because the
  collector that leaves bytes there is the one that has stopped reading - its
  window is shut, so waiting sends nothing and costs a reboot the wait.

* **The spill file could corrupt the stream it was meant to save, and that is
  older than the defect above.** A non-blocking `send()` stops mid-message, and
  the buffer was advanced by the byte count, so it could begin at the tail of a
  message - and so could the spill file written from it. Measured: 82917 bytes
  spilled, 82917 replayed on the next connection, and **one record decoded out
  of them**. The exporter now tracks how much of the front message the socket
  took, drops only whole messages, never inserts the template in front of a
  half-sent one, and drops the connection when a spill splits a message,
  because that stream can no longer be completed. The 1 MB spill and the 60
  second template refresh could both hit this on a live box today.

* **The shipped collector read an allocation as ENDED AT RESTART at a restart
  that had happened before it.** A wrong answer about a subscriber, in the one
  report this product exists to produce. Captured in a release suite run: the
  still-running box was given its block at **05:18:28** and the report said the
  allocation ended when the exporter reinitialised at **05:18:17**, eleven
  seconds earlier - the message carried its own refutation
  ([#223](https://github.com/andrediashexa/hexarouting/issues/223)).

  The rule compared the boot marker in force *at* the allocation with the
  latest one, and markers are periodic. An allocation made in the gap between a
  restart and the first marker of the new incarnation read its "init then" off
  the **previous** incarnation, so every block handed out in the first seconds
  after a reboot was read as one the subscriber had stopped holding. The window
  is the marker interval, every time a box restarts.

  What "ended at restart" means is that an incarnation started **after** the
  allocation, which is one comparison against the allocation's own timestamp
  and does not depend on when a periodic message lands. `tools/ipfix_collector.py`
  ships as `/usr/lib/hexarouting/ipfix-collector`, so this is the box's own
  reading and not a lab's.

  `lab/ipfix-stop.sh` exercises the same rule against three real boxes and had
  been passing: whether it sees the gap depends on when a marker lands, which
  is how this reached a release. `test/test_collector_marker.py` is new and
  drives the captured timeline to the second, so it fails on the rule rather
  than on the machine's mood.

  Adding that suite exposed a second thing: `tools/ipfix_collector.py` ended
  with a bare `main()`, so **importing** it started a collector - it bound
  udp/4739 and listened for 25 seconds inside `make test`, on a box where a lab
  may already hold that port. It runs under `__name__ == "__main__"` now, which
  changes nothing for the four labs and the appliance that run it as a program,
  and means the file can be read without being run. A module that cannot be
  read without being run is a module nothing can assert about, which is part of
  why the rule above went unguarded.
* **Half the boot self-test's statements had nothing holding them.** The
  self-test makes 52 statements about a box and `test/test_selftest.sh` held
  about half; the rest could stop firing, or start firing on the wrong
  condition, with nothing to say so. That is how `[ ok ] rx queues:` with no
  value after it shipped in the first place. Four of the unheld ones are now
  asserted branch by branch, chosen because each is a diagnosis an operator
  cannot make from the console on their own: the **CGNAT plugin** (a dataplane
  without it answers `show version` perfectly well and forwards nothing), the
  **`vpp.service` restart counter** (a service in a loop is `active` about half
  the times you look), the **OSI punt for IS-IS**
  ([#168](https://github.com/andrediashexa/hexarouting/issues/168): without it
  the adjacency never forms and the only evidence is a dataplane counter
  nothing surfaces), and **RPKI** (a cache configured and not connected marks
  every route notfound, quietly)
  ([#222](https://github.com/andrediashexa/hexarouting/issues/222)).

  One of those was not merely unasserted, it was unreachable: the fake box
  answered `systemctl show -p NRestarts` with the constant `0`, so neither the
  warning branch nor the looping branch had ever been executed by anything. It
  reads from the fake box now, like every other answer there.

  Three more followed, and they needed `HEXA_SELFTEST_ROOT` to cover the kernel
  command line, the cpufreq governor and the records volume, for the reason
  already written beside that variable: a machine cannot be made to disagree
  with itself about its own C-states, or about where `/var/log` is mounted,
  without rebooting it. The **latency pair** costs throughput with no counter
  behind it, and the **records volume** is a legal requirement here - `fstab`
  mounts it `nofail`, so a volume that never mounted leaves the box working
  perfectly and writing a year of records onto the root filesystem. Each also
  asserts its quiet case: a machine with no cpufreq is told there is nothing to
  set rather than warned, and the live medium is told records are not persisted
  rather than warned about a disk it does not have.

  And the one that is a lost box rather than a lost measurement: **which NIC
  the dataplane may claim**. The box once shipped `HEXA_DATAPLANE_IF=eth0`,
  which does not exist under predictable interface names, so the tuner wrote a
  DPDK stanza with no allowlist and VPP claimed whatever NIC it found - the
  management one, on a single-NIC box
  ([#123](https://github.com/andrediashexa/hexarouting/issues/123)). Four
  branches, three of which look alike from outside, and each asserted on its
  own: the first version of that check conflated two of them and failed every
  live medium.

  And the four the whole report rests on, none of which anything held: the
  **version stamp** an operator quotes when they report a fault, the **RAM**
  floor (`docs/appliance.md` records the ISO booting with 2 GB and the console
  saying "Out of memory: Killed process (vpp)" with VPP at 19 MB), the
  **instruction set** (without `sse4_2` the dataplane does not fail to forward,
  it dies with SIGILL, which reads like a crash and sends whoever is looking at
  the packet path), and **whether VPP is answering at all**, which every line
  below it depends on. Each carries the line the product actually draws rather
  than a tidier one: 3 GB is a warning and not a refusal, and a missing `avx2`
  is a throughput statement, because VPP runs on that box.

  A second round took the four that were left and were reachable: the **`vpp`
  group** (without it the only account that can configure a pool is root, which
  is the whole of #35 and #184 undone), **the API reach proven and disproven**
  as separate states, **`hexarouting.service` failing** (the box comes up
  forwarding nothing it was configured to forward while VPP, FRR and the VTY
  all answer perfectly), and **how many pools the box ended up carrying**. Two
  of those were unreachable for the same reason `NRestarts` was: the fake box
  answered `systemctl is-failed` with a constant, and it had no `show bpa pool`
  at all.

  57 checks on master before any of this, **112** after.

* **The `<- not checked` marker is shorter, and three places were still
  quoting the old text.** It shipped in 0.66.0 as
  `    <- not checked by this VTY`, which put a real statement at **89
  columns** on the 80-column serial console a racked box is seen through, so
  [#141](https://github.com/andrediashexa/hexarouting/issues/141) shortened it
  to `  <- not checked` while making it mark far more. The change did not reach
  `lab/bgp-policy.sh`, a wiki page or `docs/wiki-audit.md`, and the lab
  assertion went red the first time anything ran it after the merge - three
  days after the text changed.

  `test/test_doc_commands.py` now reads every `lab/*.sh` that quotes the marker
  and requires the quotation to be a prefix of what `config.UNCHECKED_MARK`
  actually holds. A literal the product emits and other files quote is the same
  shape `test_context_map.py` guards for paths: confidently wrong and silent
  until somebody meets it.

  The neighbouring assertion was vacuous and is fixed with it: it looked for
  `set local-preference 200    <-`, and neither that value nor that marker was
  anywhere in the lab's configuration, so it passed whatever the box did. It
  now names a statement the lab really commits and the VTY really models.

### Fixed

* **An entry the box could not write made the next one read as tampering**
  ([#236](https://github.com/andrediashexa/hexarouting/issues/236)).
  `bpa_audit_entry()` advanced the hash chain before it wrote, and advanced it
  whether or not the write happened. One entry the box could not record left
  the head pointing at a line the file does not contain and the sequence one
  ahead of it, so the next entry that DID get written landed with a gap.

  Measured, on a box whose records filesystem had gone read-only, which is what
  ext4 does to itself when the disk under it fails:

  ```
  BROKEN before entry 5
    the sequence jumps from 3 to 5: 1 entry was removed from the middle
  ```

  Nobody removed anything. That is the exact misdiagnosis the paragraph above
  `bpa_audit_verify()` says the function was built to avoid - *a verifier that
  checks the hash first reports every removal as an alteration of the innocent
  line after it* - produced by the writer rather than by an attacker, and
  handed to whoever is reading the trail for a court order.

  The chain now advances only once the bytes are in the file, so what is in the
  file is always a chain that verifies. A lost entry is counted and reported in
  those words, because a chain that is quietly short is worse than one that is
  loudly broken and the file cannot carry news of a write to it that failed:

  ```
  INCOMPLETE: 1 entry could not be written since this dataplane started, so it
  is missing from this file and it is nowhere else either. Check the disk under
  /var/log/hexarouting/config-history.log
  ```

  **A short write is taken back.** A filesystem that fills mid-write leaves a
  fragment with no newline and no digest, and both readers counted that as an
  entry written before the chain existed. `ftruncate` puts the file back to
  where the entry started.

  **And a file that is cut mid-entry is now named as cut.** The last line with
  no digest is a different event from any other line with no digest: the
  entries before the chain existed are followed by the ones that came after,
  while a fragment is the end of the file. An investigator was being told the
  trail is old where it is in fact cut.

  `lab/records-full.sh` measures all of it against a real box, and it corrected
  the reasoning behind this entry twice. **A full filesystem does not reliably
  lose the entry**: appending a few hundred bytes to a file whose last page
  still has room needs no new block, so the write fits. The lab's first run
  padded the filesystem, read `0 bytes` out of `df`, watched the entry be
  written anyway, and would have reported the product sound. It now proves the
  filesystem refuses a one byte write before it measures anything, and uses the
  read-only remount for the deterministic case.

  **Not done, with the reason:** the count is not in the boot self-test. It
  lives in this dataplane's memory and is deliberately not persisted - the
  thing that has just failed is writing - so at boot it is always zero, and a
  check that can only ever say zero is one people learn to skip.

* **The page every new reader is sent to first counted its own invariants
  three times and got two of them wrong**
  ([#232](https://github.com/andrediashexa/hexarouting/issues/232)).
  `docs/context.md` said *Eleven invariants* in its TL;DR, *The nine things
  that are not obvious from any one file* in the heading eleven lines below,
  and the README sent the reader to *the nine facts*. There are eleven: 1.10
  and 1.11 sat under a heading that excluded them, so a reader who counted nine
  and stopped had missed *The issue is the record of the work*, which is a
  working rule of this project.

  Section 5 of that page is called *How this page is kept honest* and says of
  its own test: *it does not check that the prose is still true - nothing can.
  What it catches is the half that rots silently.* A count of the file's own
  subsections is not prose. It is arithmetic over the headings in the same
  file, it is exactly that half, and it was not being caught.
  `test/test_context_map.py` now counts the `### 1.N` headings and holds all
  three statements to the number, in the words they are written in.

* **The README repeated four numbers about itself, and three had already become
  two numbers** ([#231](https://github.com/andrediashexa/hexarouting/issues/231)).
  The same file says, of the version: *deliberately not repeated here, since a
  number written in two places becomes two numbers.* Measured on master:
  `make test  # 25 suites in ~21 s` against 32 suites and 39 s; *Verified -
  35,335 checks against the Python reference model* against a suite that prints
  32,805 and 4,905 checks and 9,120 comparisons and **no program anywhere that
  prints 35,335**; and the `Layout` block listing `docs/context.md` twice.

  **The two counts were removed rather than corrected**, and that is the point
  of the entry. A suite count and a model check count go up whenever somebody
  adds a test, so a check holding the README to them would go red on the day a
  test is added, which is the worst kind of check there is. The claim each was
  making is kept: `make test` needs no root and no VPP, and the mapping core is
  checked against the reference model one mapping at a time, with the counts
  printed by the run rather than written down here.

  **The VPP pin is the opposite case and is now checked.** It does not drift on
  correct work: it changes the day the project moves to a new VPP, and that is
  exactly the day both copies must move. `test/test_doc_copies.py` reads it out
  of `tools/build.sh` and out of the README and compares them.

  The duplicated listing line hid from the repeated-sentence check added beside
  it, because that check strips fenced blocks on purpose: an example command
  repeated in two places is an example. A directory listed twice is a claim
  about the tree. Repeated whole LINES inside a listing are now compared too.

* **The roadmap told an operator that RTSP and ESP ship off, and they ship on**
  ([#230](https://github.com/andrediashexa/hexarouting/issues/230)).
  `docs/roadmap.md` section 2.3 stated the rule for ALG defaults twice. The
  first statement is the decision from
  [#14](https://github.com/andrediashexa/hexarouting/issues/14) - every ALG
  built here is on - and eleven lines below it sat the paragraph that decision
  had reversed: *SHOULD default to off unless its parse is cheap to make safe*,
  followed by *FTP, SIP and PPTP default on*. Three of the five named, and the
  box ships all five.

  The two it leaves out are the two an operator goes looking for after a
  support call: a black frame from an IP camera, an IPsec tunnel that will not
  come up. Being told to go and switch on something that is already on sends
  that diagnosis somewhere else, which is worse than saying nothing.

  The stale paragraph is gone and the argument for each of the five now sits
  with the decision rather than as an exception to a rule that no longer
  exists. `test/test_doc_copies.py` compares `BPA_ALG_DEFAULT` in
  `src/plugins/bpa/bpa_pool.h` against the registry beside it and against what
  the section claims, so a sixth ALG cannot arrive in the header with the
  document left behind.

  **And the shape, rather than only this instance of it.** The stale paragraph
  restated a whole sentence word for word from eleven lines above, which is how
  the duplication was visible without knowing anything about ALGs. The same
  file now checks that no document an operator reads says the same thing twice:
  measured over `roadmap`, `architecture`, `logging`, `appliance` and the
  README, 1723 sentences and not one repeated, so the rule costs nothing to
  hold. It was shown failing on the paragraph that started this.

### Internal

* **`docs/routing.md` sent readers to the wrong lab for four claims**
  ([#247](https://github.com/andrediashexa/hexarouting/issues/247)). The
  as-path regex cases, the two-origin policy test, the local-preference test
  and the RPKI assertion were all attributed to `lab/routing.sh` and all live
  in `lab/bgp-policy.sh`, which
  [#167](https://github.com/andrediashexa/hexarouting/issues/167) split out of
  it. A reader following the document ran the wrong lab, found nothing, and was
  left with two readings that are both wrong: that the claim is unheld, or that
  the lab has lost it. `lab/routing.sh` even states outright that it builds no
  RPKI module, so one of the four contradicted the file it pointed at.

  **The check is narrow on purpose, and the wider ones were measured before
  being rejected.** Requiring every backticked token beside a lab reference to
  appear in that lab gives 26 findings, of which almost all are correct prose:
  a document legitimately names a command it explains, or another test file. A
  check that goes red on work that is right is the worst kind there is, so that
  one is not shipped. Requiring a fixture to be in ONLY the named lab says
  nothing at all here, because these prefixes appear in several labs.

  What holds with no false positives: a paragraph that names exactly one lab
  and quotes a **fixture** - an IPv4 prefix, an AS number, an
  UPPERCASE-HYPHEN policy name - is claiming that lab uses it. Measured over
  every document: one finding, and it was one of the four defects.

* **The English-only rule had decayed to twenty-one Portuguese strings, and
  nothing held it**
  ([#246](https://github.com/andrediashexa/hexarouting/issues/246)).
  `CONTRIBUTING.md` section 1 rule 1 says documentation, source comments and
  VTY output are written in English.
  [#1](https://github.com/andrediashexa/hexarouting/issues/1) closed on it.
  Nothing checked it afterwards, so it decayed the way an unheld rule does.

  The issue reported seven strings. Measuring found **twenty-one**, in fourteen
  files, and the extra fourteen are the argument for the check rather than for
  the translation: a list assembled by grepping for the words somebody happened
  to think of is a sample, not a set. Two are worse than comments -
  `bpa audit-path [<caminho>]` is VTY help text, which is the third thing the
  rule names, and several are section headers in lab transcripts, which
  `CONTRIBUTING.md` names as the safe place to quote the product's strings from
  when writing the wiki.

  `test/test_english_only.py` holds it: a fixed list of 113 words that cannot be
  English and cannot be a protocol identifier, one of them on a line being
  enough. **Not a spell checker and not a language detector** - the false
  positive cost of either on a tree full of protocol identifiers is higher than
  the defect. `com` is deliberately left out for the same reason: it is
  Portuguese and it is also the end of every `github.com` link in the tree.

  Five identifiers are allowed with the reason written beside each: the
  `TRANSITO` peer-group, `America/Sao_Paulo`, sfdisk's `label: dos`, the
  `AS-NAO-EXISTE` AS-path list, and `uplink para o transito`, which is a
  deliberate fixture - the product must store and read back what a Brazilian
  operator actually types. The check also fails when an allowance no longer
  matches anything, because an allowance nobody needs is one nobody re-reads,
  and it caught one of mine left behind after I translated the string it was
  written for.

* **The trail's `fsync` could be deleted with 36 assertions on that file still
  green** ([#243](https://github.com/andrediashexa/hexarouting/issues/243)).
  `docs/logging.md` states it as a MUST and gives the reason in the same
  sentence: a compliance log MUST NOT live only in the page cache. Removing the
  call left all 33 suites green and `lab/audit.sh` green too, because every one
  of its 36 assertions reads the trail back **through the same kernel that
  buffered it**. A page-cache-only file reads perfectly right up until the box
  loses power, which is the one moment a retention obligation is about.

  `lab/trail-fsync.sh` measures it with no debugger and no extra package. The
  trail goes on an ext4 filesystem loop-mounted from an image file: writes land
  in the loop device's page cache, and the loop driver copies a block down to
  the **backing file** only when that block is written back, which `fsync`
  forces and the writeback timer otherwise leaves for up to thirty seconds. So
  reading the image directly, immediately, asks the box the question the
  document asks.

  **Section 3 is the control and the lab is worthless without it**: it writes a
  file to the same filesystem without an `fsync` and shows it is NOT on the
  disk. A filesystem that happened to write everything through would pass the
  claim on its own, and this lab would be reporting an `fsync` that is not
  happening.

* **Nothing could see which half of the box a commit touched first**
  ([#241](https://github.com/andrediashexa/hexarouting/issues/241)).
  `docs/routing.md` calls it a MUST and `apply()`'s own docstring repeats it:
  CGNAT is applied first and routing second, because a routing change can cost
  reachability and doing it last keeps the half-configured window short.
  Reloading FRR at the top of `apply()` left all 33 suites green.

  The reason is worth more than the fix. It was not that nobody had written the
  assertion: **nothing was in a position to write it.** The FRR stub recorded
  nothing at all, and the API stub and the dataplane stub each kept a list of
  their own, so no two calls could be compared. Two lists that each start at
  zero cannot answer which came first.

  One clock for all three now, and the check reads the single sequence. Shown
  failing on the mutation that started this - `['routing', 'cgnat',
  'routing']` - and on both halves going missing, because an ordering check
  over a trace with only one kind of entry in it passes while asserting
  nothing.

* **The 2^28 capacity bound could be removed with every suite green**
  ([#240](https://github.com/andrediashexa/hexarouting/issues/240)).
  `docs/architecture.md` calls it a MUST NOT and gives the reason: it is what
  keeps the reciprocal multiplication inside `u64`. The line above it, that
  `block_size` MUST be a power of two, is asserted at
  `test/test_bpa_pool.c:304`. This one was asserted by nothing, in a file
  `make test` compiles.

  A pool past the bound does not fail loudly. The mapping divides by capacity
  with a precomputed reciprocal, and `ref/check_magic.py` proves that
  reciprocal exact only for divisors below this number. Past it the
  multiplication leaves `u64` and subscribers are mapped to the wrong public
  address, silently.

  **Both sides of the boundary are asserted, and the second one found
  something.** A pool of exactly 2^28 subscribers must be accepted, because a
  guard written `>=` by mistake would refuse the documented limit and a check
  that only tried one-past would call that correct. Shown failing both ways:
  removing the guard accepts 268,439,552 subscribers, and `>=` refuses exactly
  268,435,456.

* **Both REQUIRED defences against a pool holding the box's own address were
  unasserted** ([#242](https://github.com/andrediashexa/hexarouting/issues/242)).
  `docs/routing.md` calls them REQUIRED and states the symptom in the paragraph
  above them: `bpa-out2in` runs on the `ip4-unicast` arc before `ip4-lookup`,
  so it sees the box's own BGP and OSPF packets first. A peering address inside
  a public pool means the box translates its own control plane and the session
  drops for no apparent reason - a dead BGP session with healthy interfaces and
  a healthy dataplane, which is the worst kind of outage to diagnose because
  nothing is broken.

  Both were implemented. Both could be neutralised with four characters each
  and all 33 suites stayed green: `bpa_check_local_overlap()` needs `vnet` and
  the unit suite links `bpa_pool.c` alone, and **no lab had ever created a pool
  over one of its own interface addresses.**

  `lab/localaddr.sh` does, on a real interface really carrying the address,
  which is what the defence reads. Nine checks, and three of them are controls
  rather than the claim: a pool that clashes with nothing is created, and an
  address belonging to no pool is not reported. Without those two, a box that
  refused every pool and logged an error for every address would pass this lab,
  and the second is what keeps the message worth reading - an error printed
  where it does not apply is one operators learn to skip.

* **A test file in the tree that nothing ran, and nothing would have said so**
  ([#235](https://github.com/andrediashexa/hexarouting/issues/235)). Two lists
  decide what `make test` runs: `test/suites.list` for the shell and Python
  suites, and the `Makefile`'s `test:` target, which names the eight C binaries
  one by one. Nothing compared either with the directory, and no test read the
  `Makefile` at all.

  Write `test/test_bpa_tcp.c`, forget the `Makefile` line, and it is never
  compiled and never run, while `make test` prints the same *N suites, N
  passed* as before, because N is what the lists produce and not what the
  directory holds. The author believes the checks they wrote are guarding the
  plugin, and nothing is. That is
  [#233](https://github.com/andrediashexa/hexarouting/issues/233) one directory
  over and worse: there the thing not running was a lab, here it is a check,
  and every other guard in this repository rests on a file in `test/` being a
  file that runs.

  **The first version of this check passed the mutation that matters most.** A
  C binary dropped from the `test:` target keeps its build rule, so the file is
  still named in the `Makefile`, still compiles when asked, and never runs -
  and matching the file name anywhere in the `Makefile` called that fine. The
  check resolves the file to the binary its build rule produces and requires
  THAT in the target, which is the thing that actually makes it run.

* **Nothing compared what the installer reads with what the ISO carries**
  ([#234](https://github.com/andrediashexa/hexarouting/issues/234)).
  `appliance/build-iso.sh` copies four top level directories into the chroot
  the installer runs inside; `appliance/install.sh` reads from four. They
  agreed, and the agreement was a coincidence that had held rather than a rule.

  A path named outside those four exists in the repository, so both directions
  `test/test_install_manifest.py` already checked stayed green, and the ISO
  build failed from inside a debootstrap with the path buried in the log -
  which is the exact case that file's own docstring says is a second's work to
  catch earlier. An installer that guards the read with `[ -f ... ]` is worse:
  the medium builds, the box installs, and whatever that file configured is
  absent, which is the `appliance/systemd/` shape one layer out.

  The third direction is checked now, and refuses to pass when either list
  comes back empty rather than reporting agreement between two things it could
  not read.

* **The lab suite excluded five labs and never said so**
  ([#233](https://github.com/andrediashexa/hexarouting/issues/233)).
  `tools/run-all-labs.sh` skipped seven names with a shell pattern. Two of them,
  `lib` and `verdict`, are the libraries the labs source and are correctly not
  labs. The other five are lab files that did not run, and no run ever mentioned
  them: between *building the plugin once* and the first result there was
  nothing to say that `crossaddr`, `hw-lab`, `hw-vpp`, `boot-iso` and
  `install-iso` had not been attempted.

  Eleven sections further down, the same file already states the rule it was
  breaking: *the lab did not run, so it has shown nothing.* It applied that to
  the accidents and not to its own exclusions, so a name added to the pattern
  would have taken a lab out of every future run with `fail=0` still printed.

  The list is data now, one `<name>:<reason>` per line, read twice: once to skip
  the file and once to print what was skipped before the first result, so the
  exclusions are in the artefact a release decision is read from.
  `test/test_run_all_labs.sh` holds it - every entry carries a reason in a
  sentence, every entry names a lab file that exists, the loop skips by that
  table and not by a pattern of its own, and the run announces it. Adding an
  exclusion now costs whoever adds it a sentence, which is the whole mechanism.

  The set of labs that run is byte for byte the same seven exclusions as before.

* **`LAB_WORKERS=2` started a box with no workers at all**
  ([#229](https://github.com/andrediashexa/hexarouting/issues/229)). The
  variable was a 0/1 flag carrying the name of a count: `startup_conf` asked
  whether it was exactly `1` and everything else - `2`, `4`, `yes` - fell
  through to the no-workers branch, silently. The lab then ran, measured a
  single-threaded box and passed, which is the shape this release keeps
  finding: a thing that looks like it did what you asked and did the opposite,
  with no line saying so.

  It cost a run on the day `lab/extblocks.sh` was repaired for the
  worker-sharding case, verifying that case with `LAB_WORKERS=2` against a box
  whose own output said `the box has 1 worker slice(s)`. Nothing was wrong with
  the repair; the harness had not started what the command named.

  Now the number is the count. `0` and unset are no workers, `1` is still the
  documented default of two so `tools/run-all-labs.sh` is unchanged, `N` above
  one is N, and a value that is not a count is **refused with what to pass
  instead** rather than read as off. `test/test_lab_workers.sh` asserts all
  five, and was shown failing against the old condition: the 2/4/8 assertions
  each reported `started none worker(s)`.

  **And the same defect one layer up, which the fix would otherwise have
  created.** `lab/icmp-tcp.sh`, `lab/dynamic.sh` and `lab/hairpin.sh` each gated
  their worker section on `LAB_WORKERS = 1`, so with a count that now really
  works, a box with four workers would have skipped every section that is about
  workers. They ask the box instead, through a new `box_workers` that counts
  `vpp_wk_` threads: a request is not an answer. `lab/dynamic.sh` also derives
  the slice it expects from that count - three blocks over N workers can serve
  `3 / N` whatever the addresses are - rather than hardcoding the number two
  workers happen to give, and `lab/hairpin.sh` asks for the two it needs to
  steer between rather than for "some". At the counts the suite runs, none and
  the default of two, every one of these is the same assertion as before.

* **`lab/igp.sh` no longer holds a daemon list of its own.** It seeds a
  fakeroot with the distribution's `/etc/frr/daemons`, runs
  `appliance/install.sh` into it, and starts exactly what the installer
  enabled. The list written out in the lab was a copy somebody had to remember
  to keep in step with the product's, which is the #99 shape one layer up and
  is precisely how `ospf6d` went missing: the lab could not fail when the
  installer's list was wrong. Shown failing - nine checks red, including every
  OSPFv3 assertion - on a scratch copy with `ospf6d` taken back out of
  `install.sh`.

* **The section of `lab/igp.sh` that asserted a refusal now asks about RIP.**
  The check itself has to keep working or removing it would pass the OSPFv3
  section just as well, and RIP is the honest subject: FRR takes `router rip`,
  this product claims nothing about it, and an operator who types it is told at
  commit rather than after an afternoon of wondering why nothing is learned.

* **`lab/frr-readback.sh` gained an OSPFv3 section, and refuted a claim written
  into this branch on its first run.** A comment saying `ospf6d` *rewrites*
  `interface <name> area <a>` into the interface stanza came from a probe that
  had typed the target spelling into that interface before looking for it -
  the trap section 4d of that file already existed to warn about, made again
  within the hour. Measured on an interface nothing else had touched, FRR
  10.7.1 answers `% Unknown command`: it is refused, not rewritten. The three
  statements FRR leaves out of its own read-back at their default -
  `ipv6 ospf6 hello-interval`, `dead-interval` and `priority` - each have a
  control at a non-default value, which is what tells an omission from a
  refusal, and they are not offered in the grammar for that reason.

### Not shipped, with the reason

* **No `CPUQuota=` on the control plane.** On this box the control plane does
  not compete with the workers: they are on isolated cores. Throttling it frees
  nothing for forwarding and costs BGP convergence. The one place the two
  genuinely contend is VPP's main thread, and the measurement that would size a
  quota there cannot be taken on a virtio VM, where the host scheduler preempts
  vCPUs whatever the guest isolates.
* **No `AllowedCPUs=` cpuset for the control plane: the bound is already
  applied by the kernel, and it is inherited rather than enforced.** Measured
  on the installed box, twice: with `isolcpus=1` on a two-cpu machine, **pid 1
  itself** has `Cpus_allowed_list: 0`, and `bgpd` has `ppid 1` and the same
  mask. A bare `isolcpus=` list restricts init's affinity at boot and every
  service inherits it, so the control plane is already confined to the cores
  the tuner left it, from the same list the tuner computed, with no cpuset
  anywhere. What a cpuset would add is the residue the same lab records:
  `taskset` onto the isolated core was **accepted**, so anything that calls
  `sched_setaffinity` leaves the set. Nothing this product ships does, and an
  operator in `start shell` who types `taskset` has said what they meant.
  One earlier run of the same lab read `0-1` and has not been explained; it
  was taken before the lab recorded pid 1's mask and bgpd's parent, which is
  the pair of numbers that would settle it, and both are now read on every
  run. `docs/appliance.md` carries the readings.

## 0.66.0 — six times a lab did the product's job, and the product now does it

[#67](https://github.com/andrediashexa/hexarouting/issues/67),
[#151](https://github.com/andrediashexa/hexarouting/issues/151) (partly),
[#182](https://github.com/andrediashexa/hexarouting/issues/182),
[#197](https://github.com/andrediashexa/hexarouting/issues/197),
[#203](https://github.com/andrediashexa/hexarouting/issues/203),
[#212](https://github.com/andrediashexa/hexarouting/issues/212),
[#214](https://github.com/andrediashexa/hexarouting/issues/214),
[#215](https://github.com/andrediashexa/hexarouting/issues/215),
[#216](https://github.com/andrediashexa/hexarouting/issues/216).

### Fixed

* **Nothing in the product created a `linux_cp` pair, so FRR saw no dataplane
  interface.** `lcp create` stood in six labs and nowhere in the product, so
  every routing feature this box ships was configurable and completely inert on
  an installed one: without a pair the port that carries traffic has no netdev,
  `ip` does not have it and zebra does not have it. `linux-name <name>` under
  `interface` creates it over the binary API. The name is the **operator's**,
  not derived, because FRR, `ip` and their own scripts act on it and #161 is the
  standing rule that such a name is read back from the box rather than invented
  ([#182](https://github.com/andrediashexa/hexarouting/issues/182)).

  Two boot defects were found behind it, by asking out loud whether the pair
  survives a reboot. Both ended with `hexarouting.service` failing and the box
  coming up with **no configuration at all**. `save` wrote `interface tap4097`
  - the pair's own TAP, which the dataplane reports like any other interface -
  and at boot VPP has no pairs, so the interface-existence check refused the
  whole file. And a loopback is created administratively **down**, so its saved
  stanza carries `shutdown`, and `apply()` shut interfaces down *before*
  creating them: every loopback
  [#179](https://github.com/andrediashexa/hexarouting/issues/179) can create
  survived exactly until the next reboot.

  The first cut of the TAP fix was nearly worse than the defect. Purging by
  **name shape** would have taken `lab/hw-vpp.sh`'s hand-made `tap0`, which
  carries an address, a route and a CGNAT attachment - and a setting lost from
  the model is a setting the next commit removes from the box
  ([#47](https://github.com/andrediashexa/hexarouting/issues/47)). The TAPs are
  read off the box with `show lcp` instead.

  A measurement refuted the obvious assertion: `vtysh -c "show interface"`
  **does** list an unpaired interface, because configuring one stages a stanza
  into FRR. The discriminator is `pseudo interface` against a real index, and
  the word the existing check looked for - `Unknown` - appears in no FRR output
  either way, so it was passing on a box with no pair at all.

* **`in RIB, not in FIB` found nothing, on every box, in silence.**
  `routes.vpp_host_interfaces()` looked for a literal `host` token and took the
  next field; no line VPP prints has such a token, so it returned an **empty
  set** - and an empty set made `compare()` suppress every route FRR holds that
  the FIB does not. The one report that exists to find a route the dataplane is
  missing found none
  ([#151](https://github.com/andrediashexa/hexarouting/issues/151)).

  The claim "the old positional parse cannot work" *was* written down, and
  demonstrated against an inline **copy** of the parse rather than against the
  function. The copy was wrong, the assertion was green, and the function
  shipped. An assertion that reimplements the thing it is checking is checking
  itself.

  The other end of the same mistake followed one merge later: the set of
  interfaces VPP owns came from `show lcp` **alone**, which names only the
  pairs, so on a box with none it was empty and the report raised the
  container's own network through `eth0`. An interface the dataplane knows by
  name is one VPP owns whether or not anything paired it.

* **A self-test line that could not fail, and eleven more.** A fresh audit
  found 52 defensible findings - 17 that cannot fail at all, 35 weaker than
  they read - out of 597 lab assertions, 529 Python and 203 C. Twelve are
  fixed. The one to remember: `lab/utilisation.sh` asserted the histogram
  exists by looking for a word that is the `sed` range's own start anchor, and
  the heading prints unconditionally while the buckets are behind `if (live)`.
  **A box with no histogram at all, and an alarm naming the wrong subscriber
  every time, passed that lab with nine green**
  ([#151](https://github.com/andrediashexa/hexarouting/issues/151)).

* **`bpa_dyn_active()` was not a missing call and not dead weight: it would
  have been wrong to call.** It cleared the idle stamp on every block a
  subscriber holds, extensions included - and `bpa_dyn_idle()` sets those
  stamps only when the session count reaches zero, at which moment every block
  is empty. A subscriber coming back with one session needs one block, so its
  extension should go back to the pool. Wiring the function in would have kept
  it owned: the leak the comment on `bpa_dyn_idle()` exists to warn about,
  arriving through the function that looked like its counterpart. Removed, with
  the reasoning left where the declaration was
  ([#216](https://github.com/andrediashexa/hexarouting/issues/216)).
[#67](https://github.com/andrediashexa/hexarouting/issues/67),
[#203](https://github.com/andrediashexa/hexarouting/issues/203),
[#215](https://github.com/andrediashexa/hexarouting/issues/215).

[#214](https://github.com/andrediashexa/hexarouting/issues/214),
[#210](https://github.com/andrediashexa/hexarouting/issues/210) (partly),
[#217](https://github.com/andrediashexa/hexarouting/issues/217).

### Fixed

* **Deleting a pool left every block it was holding open in the retention
  log.** The blocks a pool still holds are freed by the delete itself and
  never reach the reap, so the release record the ordinary path emits was
  never written for them. In dynamic mode that log is the connection record
  kept for a year, and it is read as "the last allocation before the instant
  asked about": an allocation with no release says the subscriber held those
  ports from the delete onwards, so the box answered a court order with a
  subscriber who had already been disconnected
  ([#215](https://github.com/andrediashexa/hexarouting/issues/215)).

  The delete now emits `natEvent` 17 for every block it frees, before it frees
  it, through the same queue every other block event leaves by. The record is
  stamped at the delete and not at some earlier moment the box never observed.
  Pinned blocks are the ones this bit most often, because a pinned block is by
  definition held at delete time: a pool carrying static forwards left one open
  record per forward, every time it was deleted or replaced. Deterministic
  pools are unaffected and always were, having no allocation events at all.

  Replacing a pool goes through the same delete, so a `commit` that changes a
  pool's addresses or block size now closes the records it invalidates.

* **An ICMP error about a PPTP tunnel was dropped, so PMTUD was a black hole
  for every PPTP subscriber.** A GRE packet carries no MSS to clamp, so the
  ICMP *fragmentation needed* is not one signal among several - it is the only
  one. The symptom is a tunnel that authenticates, carries small packets, and
  hangs on the first large transfer, with nothing anywhere saying why
  ([#203](https://github.com/andrediashexa/hexarouting/issues/203)).

  It had never worked, and before
  [#201](https://github.com/andrediashexa/hexarouting/issues/201) it was worse
  than a drop: the session key was read at **offset 0** of the embedded GRE
  header, which is the flags and version word - a constant. The reverse lookup
  was built out of `0x3001` and matched nothing, on any box, for the life of
  the project.

  The Call ID is at offset 6, two bytes inside the eight RFC 792 requires an
  error to quote. What was missing was somewhere to look it up: an **outbound**
  GRE packet carries the Call ID the *server* assigned, and no per-worker table
  is keyed on it - the outbound table is keyed on the subscriber's private
  address, the inbound one on the Call ID this box handed out. A shared binding
  on (public address, server's Call ID, server address) is now written when the
  call establishes and removed when it ends. Unlike the ESP binding beside it,
  this key is **unambiguous**: RFC 2637 §2.1 makes a Call ID unique within the
  server's control connection, so two subscribers sharing a public address and
  talking to the same server have different ones.

  Nothing inside the embedded GRE header is rewritten, and `lab/pptp.sh`
  asserts that on the captured packet: the Call ID in there is the server's,
  which this box never translated. It also asserts the negative halves - an
  error about a Call ID no call has reaches nobody, and the binding goes when
  the call is disconnected - because without them the delivery assertion passes
  on a box that hands every unresolvable ICMP error to whoever owns the public
  address.

  Two pieces of housekeeping came with it, each the same defect class this
  repository has already paid for. The GRE header layout was about to exist in
  two places, which is what
  [#199](https://github.com/andrediashexa/hexarouting/issues/199) was - two
  copies of one arithmetic drifting apart in this same plugin, blackholing
  every translated packet on every appliance with workers; it is now one
  `bpa_gre.h` that the data path and the ICMP path both include, and that
  `test/test_bpa_icmp.c` can reach without VPP. And the shared table was called
  `esp` while holding GRE bindings, so it is called `shared`.

* **A static port forward on a dynamic pool, by pinning the block that carries
  it** ([#67](https://github.com/andrediashexa/hexarouting/issues/67)). An
  operator who chose dynamic mode for the IPv4 savings could not sell an
  inbound port at all: the forward was refused, correctly, because in dynamic
  mode a block belongs to whoever holds it now, so a port pinned inside one
  would keep working and start reaching **a different customer** — worse than
  not working, because nothing looks broken.

  The unit to pin is the **block**, not the forward. Configuring a forward on a
  dynamic pool holds that subscriber's block: `bpa_dyn` never hands it to
  anybody else and no hold-down reaps it, for as long as the forward exists.
  The block is the one the pool's arithmetic gives that subscriber —
  `block_idx == sub_idx`, the same block a deterministic pool would give it —
  which is what lets the reverse arithmetic, the worker handoff and
  `bpa_session_static_open()` serve both modes with no second code path. An
  operator-chosen block would have broken all three, and on a box with workers
  it would have made most (address, port) pairs unpinnable for a given
  subscriber for a reason no operator can be expected to hear.

  **The pin is derived from the configuration and stored nowhere.** The
  subscriber index comes from its address, the block index is that index, and
  the worker comes from the index too — so a box that reboots comes back
  holding exactly the blocks its forwards name, because the configuration
  replay is the only thing that has to survive. A pin table would have been a
  second thing to keep in step with the forwards, which is the reason
  `bpa_pool.h` already refused a second index on them.

  **A block another subscriber is holding is refused at `commit check`, naming
  them** — taking it would end that customer's live sessions to give somebody
  else an inbound port. Pinning is logged as RFC 8158 `natEvent` 16 and
  unpinning as 17, on the **block** and not on the forward: in dynamic mode the
  log is the only record of who held a block, and two allocations with no
  release between them is the same gap `docs/logging.md` exists to close.
  `show bpa blocks` counts pinned blocks apart from blocks in use, and `show
  bpa subscriber` says which block no hold-down will take.

  **Turning the export on re-announces every pinned block**, because a
  configuration is applied pools first and export afterwards — the order every
  boot takes — and a pin made in that window would otherwise hold a block for
  ever with nothing anywhere saying whose it is. Unlike an ordinary dynamic
  block, a pinned one never comes up for release and never gets a second
  chance at a record. Only the pinned blocks, stamped at the instant the export
  came up rather than at a guessed-at earlier one.

  Evidence: `lab/forward.sh` gains a dynamic-pool section and is run **with the
  pin removed**, where its block-survival assertion has to fail — otherwise it
  is asserting something the hold-down would have given it anyway. It leaves
  the subscriber idle for **six times the hold-down** (30s against 5s) and
  keeps two unpinned blocks as a control: if those are still held, nothing was
  reaped and the section proved nothing. The reachability claim is asserted on
  the captured packet. `sh tools/lab.sh forward off` fails 16 of its checks
  (26 once [#214](https://github.com/andrediashexa/hexarouting/issues/214)
  below adds its own); the lab passes under `LAB_WORKERS=1` and joins the
  worker pass in `tools/run-all-labs.sh`, because "the pinned block is in the
  slice of the same worker the subscriber's own traffic lands on" is a
  sentence only a worker run can check. `lab/ipfix.sh` asserts all four
  records off the wire, decoded by a real collector, with the subscriber and
  port range in them — including a block pinned while the export was off.

* **A public port could be forwarded once per pool instead of once per public
  address.** A pool has as many public addresses as the operator gave it and
  hands the same port offset to one subscriber per address, so
  `203.0.113.16:1100` and `203.0.113.17:1100` are two sockets the box has
  already given to two different customers. Both the conflict check and the
  return-path lookup keyed a forward on `(protocol, port)` alone, so the first
  customer to be sold a port took it away from everybody who shares its offset
  on the other addresses - and the next one was refused by a message naming a
  customer the operator had never heard of:
  `public port 1030 is already forwarded to 10.0.0.0:80`
  ([#214](https://github.com/andrediashexa/hexarouting/issues/214)).

  On an interleaved pool with **N** public addresses that is one forward per
  port offset where there should be N. It was invisible while forwards were a
  deterministic-only feature, and
  [#67](https://github.com/andrediashexa/hexarouting/issues/67) has just made
  them work on a dynamic pool, which is exactly where an operator has many
  addresses.

  Both are now keyed on `(public address, protocol, port)`. The address was
  already in the forward - it is derived from the subscriber, not configured -
  and on the return path it is the address the packet was addressed to, which
  that node had already used to find the block. **The out2in agreement check
  stays**: the forward is still trusted only when it names the subscriber the
  arithmetic resolved independently, which is what catches a forward that
  outlived a pool redefinition. Without it, an address-blind lookup did not
  merely refuse the second customer - it handed them the first one's packets,
  and `lab/forward.sh` now asserts that it does not.

  **Removing a forward names the subscriber when the port alone is ambiguous.**
  The public address is derived and never typed, so a public port used to BE
  the name of a forward; it now names up to one per address. `bpa forward
  <pool> <proto> <port> del` removes the one forward on that port, and refuses
  when there are several, naming the subscribers and how to say which. The VTY
  already sends the private address with a removal, so a commit is unaffected.
  The VTY's own duplicate check was the same rule in the other language: it
  refused a second `forward` line on a port for **any** subscriber, which it
  cannot judge - it does not know the pool's arithmetic - so it now refuses
  only the same port for the same subscriber and leaves the rest to the device.

  Evidence: `lab/forward.sh` gains two subscribers on **different** public
  addresses of one pool given the **same** port, on the deterministic pool and
  on the dynamic one, each asserted on the captured packet to reach its own
  subscriber and not the neighbour's. The negative half is kept: two forwards
  on the same public socket are still refused, naming the socket that holds it.
  The lab goes from 28 checks to 40; `sh tools/lab.sh forward off` fails 26 of
  them.

* **`commit` said success for a routing statement FRR threw away.** `frr-reload`
  exits 0 having dropped a statement, and the box believed it - and `commit`
  returning success is the one thing in this product an operator has to be able
  to trust, because every diagnosis they make afterwards starts from it
  ([#197](https://github.com/andrediashexa/hexarouting/issues/197)).

  `commit` now reads FRR's **own running configuration** back and refuses when a
  statement this commit introduced is not in it, naming the statements.

  The check was blocked for a long time on a measured false positive, and the
  rule everybody assumed was behind it turned out to be wrong. Measured on FRR
  10.7.1, one statement at a time, each undone before the next: `ip ospf
  hello-interval 10`, `ip ospf retransmit-interval 5` and `timers throttle spf
  0 50 5000` are **omitted** from the read-back, while `ip ospf dead-interval
  40` and `ip ospf cost 10` come back. Every one of those is the FRR default.
  **Omission is per statement, not per value**, so there is no table of FRR
  defaults to chase across versions and no comparison to get right - there is a
  short list, and `lab/frr-readback.sh` is what produces it and keeps it honest
  against an FRR upgrade.

  Two further shapes came out of the same measurement and neither had been
  named. **Rewritten:** `passive-interface vppwan0` comes back as `ip ospf
  passive`, in the interface stanza rather than the router one, and `continue
  20` as `on-match goto 20`; when the rewrite is missing too, the refusal names
  what FRR would have written, because an operator told "`passive-interface` is
  missing" will grep for it and find nothing. **Negated:** `bgp default
  ipv4-unicast` is the default, so turning it on writes nothing and turning it
  off writes the `no` line.

  The comparison is against FRR's running configuration and **not** against
  `frr-reload --test`, which reports a default-valued statement under *Lines To
  Add* for ever - a check built on that calls every commit carrying one a
  failure, which is exactly why this could not be written before.

  `lab/igp.sh` carries the half that would hurt an operator: a commit of
  statements FRR does not store still says `commit complete`, on a box where
  OSPF is running perfectly, with two assertions beside it proving FRR really
  did omit and really did rewrite - so that "complete" is not simply what this
  box says to everything.

  **It shipped as a warning first, and what promoted it is a measurement, not
  a judgement.** Five encounters with real configuration had produced four
  false positives, each one extending the table of statements FRR does not
  read back verbatim, and refusing a correct commit is the exact harm
  [#208](https://github.com/andrediashexa/hexarouting/issues/208) was filed
  for. So the check went in printing `warning:` and leaving the operator their
  commit, with the promotion condition written into the code: it becomes a
  refusal when the full lab suite has run clean with it refusing. That suite
  has now run, with the refusal in force: `pass=79  fail=0  notmeasured=0  novpp=0  ran=0`, 79 runs over 57 lab files. Every lab that commits a routing statement is in that number - `routing`, `igp`, `vty`, `bgp-policy`, `commit`, `frr-readback`, `lcp`, `in-place` - and not one of them lost a `commit complete` it used to have. `commit` reports failure, names the
  statement FRR did not keep and what FRR writes for it instead, and does not
  roll back - the rest of the commit is applied and `rollback 1` is the way
  out. What is refused is the sentence `commit complete`.

  For the record, this file and `docs/routing.md` have described a refusal
  since the check was written, because the demotion to a warning changed the
  code and left both of them saying what the product had stopped doing. They
  are true again rather than newly true.

* **`commit confirmed` reverted from a timer inside the `hexash` process that
  made the commit, so logging out cancelled it**
  ([#212](https://github.com/andrediashexa/hexarouting/issues/212)). The
  deadline and the backup were instance state on that `Shell`, checked from
  its own REPL loop. The failure the feature guards against is *"this change
  cut off the session I am typing in"* - and in that scenario the session is
  exactly what does not survive. It reverted in every case where it was not
  needed and in none of the cases where it was.

  The deadline now lives on the box. A commit with a window writes
  `/etc/hexarouting/confirm-pending` - the configuration to go back to and the
  absolute time it is due, mode 0660 group `hexa-cgnat`, because it holds a
  whole configuration and every password hash in it.
  `hexarouting-confirm.timer` elapses every fifteen seconds and sixty seconds
  after boot; `hexarouting-confirm.service` carries `ConditionPathExists` on
  that file, so systemd stats one path and starts nothing at all while nothing
  is pending. What it runs is `hexash --confirm-expired`, as root.

  Polled rather than armed at commit time because the account that commits is
  not root: arming a transient unit from an operator's session would grant
  every account holding a role the ability to make the box run units, which is
  a far larger grant than the feature is worth. The cost is fifteen seconds of
  error on a deadline given in minutes. The error being removed is infinite.

* **There was no way to confirm a `commit confirmed` at all.** The message said
  to type `commit` again; `commit` with nothing left to change returned at "no
  changes to commit" with the deadline still armed, and nothing anywhere else
  cleared it. So the box also rolled back changes the operator HAD confirmed.
  Any commit that is not `commit check` now confirms an open window, and says
  so.

* **A box rebooted inside the window can tell a confirmed change from an
  unconfirmed one.** The pending file is the record of the confirmation and is
  removed at the moment the operator confirms, not at the end of the window -
  so a box restarted after a confirmation reverts nothing, and one restarted
  before its deadline still has the deadline it was given. The deadline is an
  absolute time, so a box that was off past it comes back and reverts at once.
  What the design deliberately cannot tell apart is a reboot that is the
  operator recovering from one in the middle of the maintenance: both revert,
  which is the direction where confirming costs a word and an unreachable box
  costs a journey.

### Changed

* **The VTY no longer describes a guarantee it does not provide.** A change to
  SSH or accounts is still applied as `commit confirmed 10` whether or not it
  was asked for and still says why, but what the revert IS is stated once,
  after the box has found out whether it could arm one:

  ```
  rollback at 2026-02-11T14:32:07Z, in 10 minute(s), unless you type 'commit' to confirm
      the revert is armed on the box, not in this session: logging out, losing this
      session or rebooting does not cancel it, and confirming does
  ```

  A store that cannot be written is not papered over: the in-session timer is
  still set, and the box says that is all it is worth and to keep a second
  session open. A weaker promise that is kept beats a strong one that is not.

* **The VTY banner names an open window,** with the time the box will revert.
  The operator this feature exists for is the one logging in after losing the
  session that armed it, and nothing else on that screen said the box was
  about to undo a change under them.

* **`save` inside an open window says so.** The revert puts the running
  configuration back and does not rewrite the file the next boot reads, so
  saving an unconfirmed change would revert now and come back at the next
  reboot.

* **A revert the box cannot perform stops after five attempts and says so,**
  moving the configuration to `confirm-pending.failed` and pointing at
  `rollback 1`. A unit firing every fifteen seconds printing the same failure
  onto the console forever is the same as printing nothing.

### Internal

* `test/test_hexash_confirm.py`: every check is made from a `Shell` that did
  not make the commit, because a test that asks the committing shell whether
  it still intends to revert is asking the half the failure removes.
* `lab/install-iso.sh` drives the whole thing on an installed box: an account
  created with `commit confirmed 1`, read back out of `/etc/passwd`, the
  session logged out, one minute in which nobody is logged in at all, and the
  box saying on its own console that it put the previous configuration back.
  Nothing in that minute is typed - a lab that had to trigger the revert would
  be performing the step the product is missing.

### Not shipped, with the reason

* **No custom-built kernel, and the analysis is now written down instead of
  being re-derived.** "We should upgrade or rebuild the kernel for performance"
  came back again, and `docs/appliance.md` answered only the upgrade half. It
  now answers both, and the two answers are different in kind: the upgrade is
  *not yet*, which a measurement can overturn; the rebuild is **no**, which one
  cannot. Every performance argument for a rebuild either does not touch the
  datapath (`PREEMPT_RT` bounds kernel-thread latency and the packet path has
  no kernel thread), is already on in Debian's kernel (`CONFIG_NO_HZ_FULL=y`,
  `CONFIG_RCU_NOCB_CPU=y`, `pdpe1gb`), or is an argument for a newer
  **distribution** kernel rather than for a rebuild
  ([#36](https://github.com/andrediashexa/hexarouting/issues/36)). Against all
  of them stands the cost that does not expire: the appliance would stop
  receiving kernel security updates from Debian, and this project would become
  the thing that tracks kernel CVEs by hand for a box sitting in a provider's
  rack. So the bar is a defect **caused by** the distribution kernel and
  **unfixable by configuration**, and three cheaper answers must be exhausted
  first, each of which keeps the updates
  ([#163](https://github.com/andrediashexa/hexarouting/issues/163)).

* **No new boot-tuning knobs: nine candidates are recorded as hypotheses with
  costs, and none of them is applied.** `nmi_watchdog=0`, `skew_tick=1`,
  `tsc=reliable`, `clocksource=tsc`, `nosoftlockup`, `audit=0`, the writeback
  workqueue mask, `kthread_cpus` and uncore frequency pinning are all plausible
  for a poll-mode dataplane and none of them can be justified yet, because this
  project still has no throughput measurement
  ([#28](https://github.com/andrediashexa/hexarouting/issues/28)). Switching
  them on and reporting an improvement would be asserting a gain nobody
  measured. Each row names its cost - the two watchdogs and `audit=0` are
  diagnostics on a box nobody can walk to, and `audit=0` may collide with a
  provider's own policy - so that the next proposal starts from the price
  rather than from the name.

  **What could be read here was read**, on the development machine, and it
  settled two of the nine without a NIC: `kthread_cpus` does not occur anywhere
  in the Debian 6.1 kernel image, so it is a downstream patch other
  distributions carry and there is nothing on this kernel to set; and the
  writeback workqueue mask reads `ffffffff` on 32 cpus, so kernel writeback is
  genuinely permitted on every core the tuner isolates, which makes it the
  strongest of the nine and still unmeasured. The rest could not be measured,
  and the reason is the one this file already gives for having no `CPUQuota=`:
  the machine is a KVM guest, the host scheduler preempts vCPUs whatever the
  guest isolates - 1 708 seconds of stolen vCPU time in three days of uptime -
  and a knob that saves a timer tick cannot be seen through that. The readings
  are in `docs/appliance.md` with the commands that produced them.

* **`kthread_cpus` is not merely unmeasured, it is rejected**, and that is the
  only one of the nine that #28 cannot revive. It returns to the table if the
  appliance ever moves to a kernel that has the parameter.

### Internal

* **`test/test_kernel_tuning.py`: the kernel command line is written down
  twice, and now something compares the two.** `render_grub()` builds the line
  and `docs/appliance.md` states it parameter by parameter; nothing made them
  agree, which is the shape that already cost this project the IPFIX record,
  the `docs/context.md` paths and the VTY transcripts. The suite renders the
  real tuner against a synthetic topology and asserts four things: the two
  lists of parameters are the same list, every literal value matches
  (`iommu=pt` and `iommu=on` are different machines), `HEXA_LOW_LATENCY=0`
  drops exactly the parameters the document marks conditional, and **none** of
  the candidates above is set - not on either command line, not in
  `apply-tuning`, not in `sysctl.conf`, and no candidate sysfs path is touched
  by anything under `appliance/`. That last one is what stops the list rotting
  into "we probably do that already": a knob applied without being measured and
  moved out of the table fails the suite. Nine mutations were run against it,
  each red, including one that renamed a heading - which is how the suite
  learned to fail with a verdict line instead of a traceback.

  It also found the gap it was written to find: `intel_iommu=on`, `iommu=pt`,
  `default_hugepagesz=1G` and `hugepagesz=1G` had been on every appliance's
  kernel command line since the tuner existed and were named nowhere in
  `docs/appliance.md`.

## 0.65.0 — the box refused correct configurations, exported nothing, and let you in too early

[#208](https://github.com/andrediashexa/hexarouting/issues/208),
[#209](https://github.com/andrediashexa/hexarouting/issues/209),
[#211](https://github.com/andrediashexa/hexarouting/issues/211),
[#203](https://github.com/andrediashexa/hexarouting/issues/203),
[#212](https://github.com/andrediashexa/hexarouting/issues/212).

[#208](https://github.com/andrediashexa/hexarouting/issues/208) and
[#182](https://github.com/andrediashexa/hexarouting/issues/182) in full.

### Fixed

* **Six labs created the `linux_cp` pair by hand, so none of them tested the
  product.** 0.59.0 gave the product a way to make one - `linux-name` under
  `interface`, over the binary API - and left `vppctl lcp create` standing in
  `lcp`, `routing`, `vty`, `igp`, `bgp-policy` and `ipfix-tcp`. So the half
  that had been fixed was still never exercised: every routing assertion in
  the suite went on being made on a box somebody had paired by hand. There is
  now no `vppctl lcp create` anywhere in this repository, and each of those
  labs commits `linux-name` the way a box does.

  With the labs off that line they hold it: with the pairing removed from
  `hexash`, `lab/routing.sh` fails 42 checks and `lab/lcp.sh` fails 4. Before,
  both passed.

  And the negative half, which is what the positive one was missing. The same
  loopback committed with **no** `linux-name` is on the dataplane, has no
  netdev, and FRR does not hold it. One measurement changed how that is
  asserted: `show interface` in `vtysh` **does** list an unpaired interface,
  because configuring one stages an `interface` stanza into FRR and zebra
  makes a placeholder for any interface its configuration names. It prints
  `pseudo interface` for that, and an index and flags for a real one. The word
  the old positive check looked for, `Unknown`, appears in no FRR output
  either way - so that assertion had been passing on a box with no pair at
  all.

* **A box could not reload its own saved configuration, so a pair did not
  survive a reboot.** Found by asking the boot half of #182's first
  requirement out loud: save the file, take the pair down, hand the box back
  what it wrote. Two separate defects, both of which end with
  `hexarouting.service` failing and the box coming up with **no**
  configuration at all - no pools, no addresses and no pair - on a machine
  nobody is watching.

  The first: the dataplane reads report every interface VPP has, and once the
  product makes pairs one of them is the pair's own TAP. So `save` wrote

  ```
  interface tap4097
   mac-address de:ad:00:00:00:07
  ```

  into the file, and at the next boot - a fresh VPP with no pairs yet -
  `tap4097` did not exist and the interface-existence check refused the file
  whole. The Linux end of a pair is now taken out of the configuration at both
  ends: off the device, so no new file carries one, and out of a file the box
  is handed, so the boxes that have already saved one still boot.

  Which TAPs those are is **read off the box**, never guessed from the name.
  `tap<n>` is equally what an operator's own `vppctl create tap id 0` makes -
  `lab/hw-vpp.sh` runs its whole WAN over a `tap0` with an address, a default
  route and a CGNAT attachment on it - so a purge by name alone would take a
  real interface out of the running configuration and the next commit would
  remove it from the device, which is
  [#47](https://github.com/andrediashexa/hexarouting/issues/47). On the device
  side the TAPs come from `show lcp`; on the file side only a `tap<n>` the
  dataplane does **not** have is dropped.

  The second: a loopback is created administratively **down**, so its saved
  configuration carries `shutdown` - and the apply order shut interfaces down
  before it created them, so the first statement applied named an interface
  the dataplane did not have yet and the commit failed. Every loopback this
  product can create ([#179](https://github.com/andrediashexa/hexarouting/issues/179))
  therefore survived exactly until the next reboot. An interface this same
  delta is about to create is now shut down **after** it exists; for one that
  already exists the ordering is unchanged, because an interface that does not
  exist yet is carrying no traffic and there is nothing to stop.

* **Two lab assertions passed whether or not the interface existed.** `vppctl
  show interface loop0` for an interface the dataplane does NOT have prints an
  error containing the name it was asked about, so `grep -q loop0` matched
  either way: `lab/routing.sh` checked that a loopback was on the dataplane and
  would have said so on a box with no loopback at all. Found by breaking
  `create_loopback` and watching the check stay green. Both now require the
  name to be the first field of a line, which is where `show interface` puts
  one.

* **The boot self-test said nothing about a dataplane port with no pair.** It
  already reported the routing daemons and the binary API, and a box whose
  routing cannot work has to say so before somebody configures routing on it.
  It now reads `show lcp` against the dataplane's own interface list and names
  the ones with no pair, with the statement that fixes them. A failed check -
  not a warning - when routing is configured and nothing at all is paired,
  because that box cannot do what it has been told to do; a warning otherwise,
  since CGNAT with no routing is a configuration this product supports and
  failing it would be a failure nobody reads. `local0`, the packet generator
  and the Linux end of a pair are not counted. Read from the box and never
  arranged, which is
  [#184](https://github.com/andrediashexa/hexarouting/issues/184)'s rule.

* **The VTY refused BGP unnumbered, and failed the whole commit for it.**
  `neighbor vppwan0 interface remote-as external` — eBGP to a directly
  connected peer with no address on the link, and the ordinary way to build a
  handoff without spending a /30 — was answered with "vppwan0 has no
  'remote-as'", with the AS on that same line. The parser read `neighbor <x>
  remote-as <asn>` by position and did not know the form where the neighbour
  is an **interface** and `external`/`internal` are values. Because a refusal
  fails the candidate whole, a CGNAT or interface change typed in the same
  commit went down with it, and there was no way round it except `vtysh`,
  which is the tool this VTY exists to keep an operator out of.

  The interface name is now validated the way
  [#205](https://github.com/andrediashexa/hexarouting/issues/205) established
  for `passive-interface`: through the linux_cp pair, because it is an FRR
  statement and must be a spelling FRR can act on. A name the box does not
  have at all is refused; the dataplane's end of a pair is warned about, not
  refused, because that is the name `show interface` prints. The half of the
  check that was always right — a neighbour declared with no `remote-as`
  anywhere — still refuses, and now covers the unnumbered form too, where FRR
  accepts the statement and leaves the peer Idle at AS 0 forever.

  **The session does not reach Established yet**, and the reason is in the
  dataplane rather than in any of this: measured on `lab/routing.sh`'s pair,
  the far end's Router Advertisements reach the wire and never reach the TAP,
  because `ff02::1/128` is a receive-only MFIB entry and linux_cp punts
  nothing on the `ip6-multicast` arc. FRR learns an unnumbered peer's address
  from that advertisement and from nothing else. That punt gap is the same
  shape as [#168](https://github.com/andrediashexa/hexarouting/issues/168) and
  is a defect of its own
  ([#208](https://github.com/andrediashexa/hexarouting/issues/208)).

* **A route-map naming a policy list that does not exist yet was refused.**
  Measured on FRR 10.7.1: the clause is accepted, stored, and bound when the
  list arrives, with nothing retyped — so writing the map first and the lists
  in the next commit is a correct order of work, and refusing it took every
  unrelated change in the same commit with it. It is now a warning, which is
  the posture `rpki_findings` already argued for a clause that will not do
  anything *yet*. The true positive survives: a list name with a typo in it
  matches nothing forever, and nothing else on the box mentions it. The
  legacy `ip community-list` / `ip as-path access-list` spellings were checked
  against the same FRR and answer "Unknown command", so there was nothing to
  be tolerant of
  ([#208](https://github.com/andrediashexa/hexarouting/issues/208)).

* **A half-typed `bgp community-list standard` crashed the VTY.** It raised
  `IndexError` — a Python traceback in front of an operator in a maintenance
  window is not a refusal. Every truncation of every statement these functions
  parse, at every word and every character boundary and in every block
  context, is now fed back in by the test suite: 305,946 inputs, no traceback
  ([#208](https://github.com/andrediashexa/hexarouting/issues/208)).

* **Route-map clauses were judged in written order while FRR uses `seq`.**
  Inserting a low-seq clause after a catch-all — which is what sequence
  numbers are for — was reported unreachable when FRR reaches it first;
  measured against FRR's own `show route-map`. `on-match next`, `on-match
  goto` and the `continue` an operator types for the second were parsed and
  then ignored, so one of them poisoned the verdict on every later clause: a
  route-map containing one now gets no reachability claim at all, because
  where the reading goes next depends on whether the clause matched. `continue`
  was not a word the parser knew, so it ended the clause and a `deny` clause
  carrying a `set` after one was not refused
  ([#208](https://github.com/andrediashexa/hexarouting/issues/208)).

* **UDP IPFIX export had never worked on an installed box, and the box said
  `commit complete`.** In dynamic mode the allocation log *is* the connection
  record the Marco Civil requires to be kept for a year, and on a shipped
  appliance it could not be exported over UDP at all: VPP's own IPFIX exporter
  needs a collector, nothing in the installer, the tuner, the unit files or
  the VTY ever set one, and the dataplane therefore refused `bpa logging
  enable` — permanently, because it was not a state an operator could leave.
  Until [#76](https://github.com/andrediashexa/hexarouting/issues/76) landed
  the VTY discarded that refusal and printed `commit complete`
  ([#209](https://github.com/andrediashexa/hexarouting/issues/209)).

  `nat cgnat logging udp <collector> src <source> [port <n>]` is the statement
  that was missing. It goes to VPP's own `set_ipfix_exporter` over the binary
  API and not onto the plugin's message, because the exporter is core state
  and `libvnet` exports no setter a plugin could call — copying the core
  handler into the plugin would be a copy that drifts with every VPP bump. The
  plugin reads it back in `show bpa config`, so the destination is saved,
  rolled back and audited like every other setting, and `nat cgnat logging udp
  disable` clears it.

  Three things travel with it. **`commit check` now refuses `logging enable`
  with no exporter**, before anything is applied, rather than letting the
  dataplane refuse it half way through the apply. **The saved configuration is
  written exporter-first**: `cgnat logging enable` sorts alphabetically before
  the statement that makes it possible, so a file written in plain sorted
  order refused its own second line on reload — on the boot path, which is the
  one nobody types by hand. And **the boot self-test says whether export is
  running**, not merely configured, by comparing the file the box was loaded
  from against the state the dataplane holds: a refused setting looks exactly
  like a working one, which is the shape of
  [#172](https://github.com/andrediashexa/hexarouting/issues/172).

  How it survived: `lab/ipfix.sh` exercised the UDP path and **configured the
  exporter itself**, with `vppctl`, because it needed one — the product's
  missing step performed by the lab, which is the shape of
  [#99](https://github.com/andrediashexa/hexarouting/issues/99),
  [#168](https://github.com/andrediashexa/hexarouting/issues/168),
  [#182](https://github.com/andrediashexa/hexarouting/issues/182) and
  [#184](https://github.com/andrediashexa/hexarouting/issues/184), and the
  fifth time this repository has been caught by it. The lab now configures the
  exporter **through the VTY** and asserts a record decoded off the wire on a
  box the product configured; `sh tools/lab.sh ipfix no-exporter` leaves the
  statement out and fails 9 of its 13 checks.

  Measured against `vpp v26.06-release` while doing it, and written down in
  `docs/logging.md` because none of it is discoverable: the exporter starts
  **zeroed**, so there is no dataplane default to read back and no "leave it
  alone" encoding; `set_ipfix_exporter` refuses a source address of `0.0.0.0`
  whatever the collector is, and a path MTU outside 68–1450; clearing the
  exporter is a collector of `0.0.0.0` with the source left in place; a
  collector port of 0 is *accepted*, so the caller has to supply 4739; and
  clearing the exporter leaves `bpa logging` reporting **enabled**, which is
  why `show cgnat logging` now prints where the records go and why the
  self-test reads that line and not the state.

* **The box offered a login prompt before it was ready, and the first
  `configure` poisoned every commit in that session.** Two defects wearing one
  hat, and the second is what made the first expensive
  ([#211](https://github.com/andrediashexa/hexarouting/issues/211)).

  `Vpp.run()` reported success whenever `vppctl` produced output, ignoring its
  exit status — and `vppctl` exits 0 whenever VPP *answered*, including when it
  answered with a refusal. So the error text came back as though it were
  configuration. `configure` builds the candidate by reading the box, so a
  dataplane that was not answering put "Could not find VPP CLI socket" into the
  candidate **as a configuration line**, and every commit in that session was
  then refused with "there are configuration lines this VTY does not
  understand". The operator meets that refusal on their first real commit, by
  which time they have typed a pool and have every reason to think the pool is
  what was refused. `configure` now refuses to build a candidate from a
  dataplane that is not answering, and says the box is probably still starting.

  The prompt should not have been there at all. `appliance/build-iso.sh` wrote a
  single getty drop-in carrying **both** the ordering that holds the prompt back
  until the boot self-test has run and the autologin the live image needs. The
  installer deletes that file, correctly, because an installed box must not log
  anybody in without a password — and it took the ordering with it. So the
  ordering the live image has had since
  [#93](https://github.com/andrediashexa/hexarouting/issues/93) is exactly what
  an installed box never had. Two files now: `10-after-selftest.conf` belongs to
  every box, `20-autologin.conf` to the live image alone, and the installer
  names the second one exactly.

  `lab/install-iso.sh` used to start at "HexaRouting ready." and its comment
  described this defect, stepped around it, and told whoever came next not to
  move the wait earlier without dealing with the bug first. It now starts where
  the operator starts — the **first** prompt the box offers — and does the
  smallest thing an operator does: `configure`, then `commit`, with nothing
  typed in between. Shown failing on a full ISO build, install and second boot
  before the fix, both assertions red.

### Internal

* **The changelog said thirteen things twice, and the release gate now refuses
  it.** The mechanism is mechanical rather than careless: a branch cut while
  the top section is still called `## Unreleased` is merged after master has
  renamed that same section to `## <VERSION>`, so git sees the branch's copy of
  every bullet as an **addition**, raises a conflict only over the heading, and
  drops the whole body back into the released section. The merge looks clean.
  Each of the thirteen was told apart by asking v0.62.0, v0.63.0 and v0.64.0
  what their own CHANGELOG said, not by reading the two copies and preferring
  one. The tags keep their duplicates - they are published artifacts, and
  rewriting one would be worse than the defect.

* **`lab/install-iso.sh` gained `expect_one_of()` and `printed_before()`.** The
  first is for a box that has two answers where one of them is the defect:
  waiting for the good one turns a refusal printed in a second into a
  five-minute timeout, and the report then blames the timing for something the
  screen said at once. The second compares **byte offsets** rather than line
  numbers, because a login prompt has no line ending of its own and whatever
  the box printed last shares a line with it.

## 0.64.0 — the labs ran the box the way it ships, and seven things fell out

[#14](https://github.com/andrediashexa/hexarouting/issues/14),
[#67](https://github.com/andrediashexa/hexarouting/issues/67),
[#76](https://github.com/andrediashexa/hexarouting/issues/76),
[#165](https://github.com/andrediashexa/hexarouting/issues/165),
[#189](https://github.com/andrediashexa/hexarouting/issues/189),
[#197](https://github.com/andrediashexa/hexarouting/issues/197) (partly),
[#198](https://github.com/andrediashexa/hexarouting/issues/198),
[#203](https://github.com/andrediashexa/hexarouting/issues/203) (partly),
[#204](https://github.com/andrediashexa/hexarouting/issues/204),
[#205](https://github.com/andrediashexa/hexarouting/issues/205),
[#206](https://github.com/andrediashexa/hexarouting/issues/206),
[#207](https://github.com/andrediashexa/hexarouting/issues/207),
[#210](https://github.com/andrediashexa/hexarouting/issues/210) (partly).

### Added

* **A SIP ALG, over UDP, and it says so.** `alg sip on|off` per pool, on by
  default ([#14](https://github.com/andrediashexa/hexarouting/issues/14)). It
  rewrites the topmost `Via` sent-by, the `Contact` URI host, the SDP `c=`
  address and each SDP `m=` port — reserving a consecutive RTP/RTCP pair for
  each stream through the same machinery the RTSP `SETUP` uses, because an ALG
  that rewrites SDP and opens no mapping delivers signalling and no audio. It
  recomputes `Content-Length`, which is a third length over the same bytes that
  neither IP nor UDP can see. On the way back it restores the response `Via`
  the phone insists on (RFC 3261 §18.1.2).

  One rule keeps a small parser safe in front of a large protocol: **a span is
  rewritten only when the literal IPv4 address in it is the address the session
  belongs to.** A response the subscriber sends carries the caller's `Via` and
  is therefore not touched, with no direction flag needed to work that out; a
  CPE that already knows its public address is not interfered with, which is
  the failure operators know as "the ALG broke my phone"; and a message whose
  syntax does not come apart is refused **whole**, counted, and passed through.
  `From`, `To`, `Call-ID`, `Route`, the SDP `o=` line and the `received=` and
  `rport=` parameters are deliberately left alone, each for a reason recorded in
  `bpa_sip.h`.

  **TCP is not handled, as a decision rather than an omission.** A SIP message
  over TCP is a stream whose end one segment cannot show, and an INVITE with SDP
  regularly exceeds one; the honest answer is reassembly, which is different
  work. A TCP connection to 5060 is translated like any other and never
  inspected — and because the ALG is UDP-only it carries none of the sequence
  correction of `bpa_seq.h`: what a UDP payload rewrite invalidates is the IP
  total length, the UDP length and the UDP checksum, and that is the whole list.

  Evidence: `lab/sip.sh`, which asserts on the captured packet — every rewritten
  field, every field that must not have moved (byte for byte against the payload
  as it was injected), both lengths, the UDP checksum, `Content-Length` against
  the body that arrived, and the media reaching the phone on the announced
  ports. `sh tools/lab.sh sip off` fails exactly 7 of its 18 checks and the
  lab header names which; it also passes under `LAB_WORKERS=1` and is in
  `WORKER_LABS`. `test/test_bpa_sip.c` unit-tests the parser's refusals and the
  multi-edit offset arithmetic without VPP.

### Changed

* **The last two CLI writes move to the binary API**, so `show` passthrough is
  the only remaining use of that socket and it is read-only — checkable now,
  by a lab that lists what is **allowed** rather than what is forbidden. This
  is an **authority** boundary and not an availability one; none of #172's
  three failures is addressed by it, and the code, the docs and the lab all say
  so.
* **A dynamic pool stops claiming a capacity it cannot deliver.** Blocks are
  sharded per worker and a subscriber draws only from its own worker's slice,
  so `usable` was a ceiling nobody is promised; the smallest slice is printed
  beside it. The mechanism is unchanged and the decision is the maintainer's.
* **Any lab can be run with workers** (`LAB_WORKERS=1`), and the suite runs the
  forwarding labs both ways.

### Fixed

* **The IS-IS IPv6 warning fired on a working configuration, including this
  repository's own lab.** `ipv6 router isis <tag>` without `topology
  ipv6-unicast` was reported as "the IPv6 half carries nothing while IPv4
  works". That statement turns on Multi-Topology IS-IS (RFC 5120), a second SPF
  for IPv6; without it IPv6 reachability floods in the single legacy topology
  and forwards perfectly well when both ends agree, which is the ordinary
  single-vendor core. `lab/igp.sh` commits exactly that configuration and then
  watches `2001:db8:19::/64` reach the VPP IPv6 FIB over the adjacency, so the
  box was contradicting its own acceptance test on every run — and, because the
  check runs at every commit, printing the same sentence again over an OSPF
  NBMA neighbour, a BGP refusal and `router ospf6`. The warning is withdrawn
  rather than narrowed: what would be a real fault is Multi-Topology on at one
  end and off at the other, which is not in this box's configuration at all.
  The lab now asserts the commit stays silent about it, so it cannot come back
  unnoticed ([#198](https://github.com/andrediashexa/hexarouting/issues/198)).
* **Two more IGP findings fired on correct configurations.** The IS-IS
  `metric-style wide` warning charged every interface metric on the box to
  every instance, so a metric a wide instance expresses perfectly named a
  narrow instance that never carries that port; it is now read from the
  interfaces that joined the instance being judged. The OSPF "no area 0"
  warning ignored `area <n> virtual-link`, which is precisely how RFC 2328
  section 15 attaches a box with no physical backbone interface — the warning
  described a working design back to the operator who had to think hardest
  about it.
* **`tools/wiki-pull.sh` and `tools/wiki-push.sh` did not round-trip.** The
  mirror writes each page as `# <title>` followed by the body; Outline keeps
  the title in a field of its own, and push sent the file verbatim — so a page
  that made the documented pull, edit, push cycle grew a second title above its
  opening sentence, which `PADRAO-DOCUMENTACAO.md` section 1 forbids, and every
  further round trip added another. push now strips a leading heading that
  matches the live document's title, and **stops** when the first line is a
  heading that does not, rather than guessing. `test/test_wiki_roundtrip.sh`
  drives both scripts against a stubbed `curl` — there is no wiki and no token
  in the test — and compares the bytes that would have been sent against the
  bytes the page held, twice around
  ([#165](https://github.com/andrediashexa/hexarouting/issues/165)).
* **A session pointer was held across a second `pool_get`, in two places.**
  `bpa_alg_reserve_pair()` kept the pointer the first `bpa_session_bind()`
  returned across a second one, and that call ends in `pool_get_zero()`, which
  may move the whole session vector — so both writes below it landed in memory
  the allocator had already given back. The visible consequence was the smaller
  half: one of a reserved media pair kept `is_expect = 0`, sat on the UDP
  timeout instead of the ALG's, and an announced port nobody used was held for
  hours rather than seconds. Intermittent by construction — it needs the second
  bind to cross a growth boundary — which is why it read as a loaded runner and
  why it survived. Second instance on the PPTP control path, where both callers
  dereference their own session immediately after the ALG may have created a
  GRE session. `bpa_ftp.c` had written the lesson down; it had not been applied
  everywhere.
* **PMTUD works for an IPsec tunnel.** An ESP tunnel has no MSS to clamp, so
  when a router on the path cannot carry the subscriber's packet the ICMP
  `fragmentation needed` is the whole of what tells them, and this box dropped
  it: the tunnel comes up, small packets work, and the first large transfer
  hangs. The protocol with no identifier of its own turns out to be the easy
  one — an ESP session is keyed on (our public address, peer) and **both** are
  in the embedded IP header of the error, so it carries the whole key, needs no
  payload bytes at all, and resolves on whatever worker it landed on. GRE stays
  refused, with the reason now written where somebody will look for it.
* **`nat cgnat logging enable` was refused by the dataplane and the box said
  `commit complete`.** The status of the CLI write was discarded. It now says
  what the dataplane said — and that refusal turns out to be permanent on a
  shipped box, because nothing in the product ever configures VPP's UDP IPFIX
  exporter ([#209](https://github.com/andrediashexa/hexarouting/issues/209)).
* **`bpa pool ... alg <x> off` was accepted and ignored**, so the pool came up
  with the ALG on — and it took `lab/ftp-alg.sh`'s whole `off` mode with it,
  which the file's own header says must fail and which passed.
* **The OSPF check warned about the only spelling FRR acts on.**
  `passive-interface` has to be written with the name FRR knows, and the check
  matched it against the dataplane's names — so the correct spelling triggered
  the warning and the wrong one silenced it. Two more came out of the same
  reading: FRR does not store `passive-interface` at all (it rewrites it to
  `ip ospf passive`, so every commit after the first saw a spelling the parser
  did not know), and `passive-interface default` was read as an interface name.
* **A commit that asked FRR for changes and got none no longer says `commit
  complete`.** The general read-back check cannot be built on this tooling and
  the measurement is on the issue; what is decidable is the worst case, and it
  is now caught.

### Internal

* **No lab proved a role on a box somebody receives, only in a container.**
  `lab/install-iso.sh` booted the disk it had just installed and only *read*
  the console. Every role assertion in this repository lived in `lab/vty.sh`:
  in a container, against a VPP the lab had started itself - the shape of
  [#99](https://github.com/andrediashexa/hexarouting/issues/99),
  [#168](https://github.com/andrediashexa/hexarouting/issues/168),
  [#182](https://github.com/andrediashexa/hexarouting/issues/182) and
  [#184](https://github.com/andrediashexa/hexarouting/issues/184), and the
  shape that cost three shipped defects in two days when the same assumption
  was made about VPP workers.

  Phase 2 now types at the same serial line an operator is handed. It logs in
  as root, creates two accounts through the VTY - one `role administrator`,
  one with no role at all - commits, and then reads what the **box** says they
  got: `id -nG` out of `/etc/group`, the login shell out of `/etc/passwd`, the
  mode and group of `/etc/hexarouting` and its rollback store after root's own
  commit. There is no `useradd`, no `usermod` and no writing to the disk
  image anywhere in it, so the memberships asserted are the ones `commit`
  wrote.

  The administrator then logs in **as itself** and applies a routing change
  and a CGNAT pool, and both are read back from the daemons rather than from
  what the VTY printed: `vtysh -c "show run"` for the statement,
  `vppctl show bpa pool` for the pool. The pool goes over the binary API,
  which is root-only unless the drop-in on `vpp.service` group-owns
  `/dev/shm/global_vm` - and this is the only lab in the repository that runs
  under systemd at all, so it is the only place that drop-in is exercised. The
  rollback revision recorded for that commit is asserted by **owner**, not by
  count: root's commit wrote one a moment earlier, so a count would pass on
  the box [#188](https://github.com/andrediashexa/hexarouting/issues/188)
  describes. The account with no role is refused `configure` and
  `start shell`, and still reads both halves of the running configuration
  through the group memberships every account gets. Finally the boot self-test
  is re-run on the same console: a freshly installed box says it has no
  operator account, and the same program afterwards names the account that
  reaches the API.

  The driver is a named pipe as qemu's stdin. Every wait is for a string the
  **box** prints, with its own timeout, and a failure names the string it gave
  up on, prints the console since the last thing it recognised, and says which
  of four things happened: the box refused the credentials, refused the
  command, went silent, or answered something else. A login that fails must
  fail the lab rather than run the rest of the session against a login prompt,
  so the wait after the password is for the VTY's banner and not for a prompt.
  `sh tools/lab.sh install-iso wrong-password` types a password the box
  rejects, and the lab fails at that wait with the diagnosis - which is how a
  driver whose failure mode would otherwise be a hang is kept honest
  ([#189](https://github.com/andrediashexa/hexarouting/issues/189)).

* **`tools/lab.sh` installed neither `sfdisk` nor `unsquashfs`, so the same lab
  gave two answers depending on which command ran it.** `make test-install`
  installs both; this runner installed `fdisk` and not `squashfs-tools`, so
  under `sh tools/lab.sh install-iso` the check that the live image carries no
  usable root password could not read a byte of the image and reported that as
  a failure of the install. The lab's own `fdisk` comment already records this
  shape.

### Found and not fixed

* **A getty offers a login before the box is ready, and `configure` typed at
  that prompt makes every commit in the session fail.** Measured on the
  console of a box `lab/install-iso.sh` had just installed:
  `serial-getty@ttyS0` draws `cgnat-lab login:` while `hexarouting.service` is
  still inside `wait-for-vpp`, so the first `configure` builds its candidate
  from vppctl's own `Could not find VPP CLI socket`. That text lands in the
  candidate as a configuration line the VTY does not model, and every `commit`
  afterwards is refused with *"there are configuration lines this VTY does not
  understand"*. The VTY recovers the dataplane on its own - the console says
  *"the dataplane answered after 14s"* - and the poisoned candidate survives
  it. The way out is `exit` and `configure` again, and nothing says so. The
  failure is in reach of every operator who has just been handed a box: it is
  the first thing they type.

  Two halves, and the second explains why the first is easy to hit.
  `running_config()` must not parse a failed read as configuration - a
  dataplane that could not be read is not a dataplane with no CGNAT
  configuration. And the ordering that puts the self-test's report on the
  screen before the login prompt
  ([#93](https://github.com/andrediashexa/hexarouting/issues/93)) exists on
  the **live image only**: `build-iso.sh` writes it into the same drop-in file
  as the live autologin, and the installer deletes that file wholesale,
  correctly, to remove the autologin. So an installed box prints its self-test
  over the login prompt - the exact symptom #93 was filed for - and the
  operator's Enter lands at a prompt drawn before the box was ready.

* **A commit that changes accounts becomes `commit confirmed 10`, and the
  timer dies with the session.** Measured on the same console: creating the
  operator accounts printed *"applying it as 'commit confirmed 10': it reverts
  on its own unless you enter 'commit' again"*, and the revert is driven by
  `check_confirm_timeout()` inside the running `hexash`. Log out - which is
  what an operator does after creating an account - and nothing reverts
  anything. The safety net is real only for a session that stays open, which
  is the opposite of the case it exists for (a change that cut the session
  off).

## 0.63.0 — the configuration the product ships had never been tested

[#48](https://github.com/andrediashexa/hexarouting/issues/48),
[#170](https://github.com/andrediashexa/hexarouting/issues/170),
[#172](https://github.com/andrediashexa/hexarouting/issues/172),
[#176](https://github.com/andrediashexa/hexarouting/issues/176),
[#180](https://github.com/andrediashexa/hexarouting/issues/180),
[#199](https://github.com/andrediashexa/hexarouting/issues/199),
[#200](https://github.com/andrediashexa/hexarouting/issues/200),
[#201](https://github.com/andrediashexa/hexarouting/issues/201).

### Added

* **`lab/oom.sh`: the kernel is shown making its choice, both ways.** Real VPP
  and one growing control-plane process share a cgroup with a `memory.max` of
  their own, so the shortage is real and the lab's own shell is not in the
  draw. With the drop-in's adjustment applied the kernel kills the control-plane
  process and VPP goes on answering with its pool; with it removed and **nothing
  else changed** the kernel kills VPP, although the balloon is the smaller of
  the two by design; restored, it kills the balloon again. The number is read
  out of `appliance/systemd/vpp-oom-protect.conf` and never typed in the lab,
  and the lab checks that `install.sh` puts that same file under
  `vpp.service.d`, so deleting the drop-in fails the lab rather than leaving it
  green. `sh tools/lab.sh oom off` fails 10 of its 24 checks.
* **The boot self-test says whether the dataplane is actually out of the OOM
  killer's selection**, read from `/proc/<vpp pid>/oom_score_adj` on the running
  box and never written by the self-test. A drop-in that was not installed, or
  that systemd refused, used to look exactly like one that was working. A
  merely lowered score is reported as still selectable rather than accepted, an
  unreadable one is a failed check rather than a silence, and a VPP running
  outside `vpp.service` is named because no drop-in on that unit reaches it.
  `lab/install-iso.sh` reads the line back off a box it installed and booted,
  which is the only place in this repository where real systemd applies the
  unit.

* **The OOM killer does not count virtual memory**, and both this repository's
  documentation and the drop-in's own rationale said it did. Measured: VPP with
  17 GB mapped and 354 MB resident inside a 502 MB cgroup has `oom_score` 670
  out of 1000, its resident share, and the kernel's kill line reads
  `total-vm:17476328kB, anon-rss:247984kB`. So raising `session-table memory`
  raises VPP's badness only as the table is used. The protection stands for the
  plainer reason, which is the one the original event showed: on a box where
  nothing else is resident, the dataplane is the biggest thing there.
* **What a full BGP table costs is now measured**, and `frr.service` still
  carries no bound because of it rather than for want of a number. One full
  IPv4 table over one session, 1M prefixes with realistic attribute diversity:
  `bgpd` 837 MB and `zebra` 553 MB, against 37 MB for all of FRR on a box
  taking default routes only. A `MemoryMax=` guessed low kills rather than
  degrades - `bgpd` under `memory.max=512M` was killed ten seconds into
  convergence at 428 MB, and `frr.service` restarts it every five seconds -
  and `MemoryHigh=` is no softer: under `memory.high=512M` the same convergence
  had not finished after nine minutes, with `bgpd` in `D` state having used 6 s
  of CPU and `vtysh` returning nothing in 30 s. `docs/appliance.md` carries the
  numbers and recommends 16 GB for a full-table box instead of a bound.

* **`show ospf neighbor`, `show ospf interface`, `show isis adjacency` and
  `show isis interface`** — the IGP half of what
  [#148](https://github.com/andrediashexa/hexarouting/issues/148) did for BGP.
  `show ip ospf neighbor` was handed to `vtysh` untouched and FRR's table is
  not the table that forwards, so each of these puts the count of IGP prefixes
  that reached the **VPP FIB** on the same screen as the adjacency state. OSPF
  was not in the `show` tree at all: `show ?` offered `bgp` and `isis` and not
  OSPF, and both IGP entries ended the command. FRR's own spellings still work
  and still reach FRR — bare `show ip ospf` is still the process summary an
  operator arriving from another vendor expects.
* **An OSPF adjacency that stops in Exchange is told why.** OSPF compares the
  MTU in Database Description packets and drops any claiming one larger than
  its own, with no log line on either side; on this box `linux_cp` starts the
  TAP at 9000, so what ospfd advertises is more often wrong than right. The
  screen names the state, the MTU this end is advertising, and — when the
  box's own two halves disagree — the sentence `commit check` already printed,
  from the same place, because two diagnoses of one fact that disagree are
  worse than one. `show ospf interface` carries both MTUs in two columns.
* **An IS-IS circuit that is Up and Active with no adjacency is told why.**
  IS-IS is not carried over IP, so without `lcp osi-proto enable 0x83` the
  hellos never reach isisd and the only evidence is a VPP error counter
  ([#168](https://github.com/andrediashexa/hexarouting/issues/168)). The screen
  says whether the dataplane is punting, and counts the frames `osi-input`
  dropped as an unknown OSI protocol. It says so either way: "the punt is on"
  is what stops an operator chasing this box's best-known trap and sends them
  to the far end.
* **This box advertising the IS-IS overload bit is reported**, read from the
  LSP it is flooding rather than from the configuration that asked for it —
  a maintenance drain reported by the words that requested it is reported by a
  line that may not have taken effect. `lab/igp.sh` asserts it in the
  **neighbour's** copy of this box's LSP, and asserts that taking it off
  reaches the neighbour too.
* **`clear ospf process`, `clear ospf neighbor <id>` and `clear isis
  adjacency [<system-id>]`**, administrators only, the way `clear bgp` is.
  Asserted in the lab by what they do — the IS-IS adjacency's flap count going
  up, the OSPF adjacency's uptime going backwards — never by what they print.
* **`passive-interface` did not silence the OSPF MTU warning** it exists to
  silence, and neither did `ip ospf mtu-ignore`. Both name the interface the
  way FRR does - the Linux name - while the MTU being compared is the
  dataplane's and keyed by the dataplane's name, so neither ever matched. The
  escape hatch an operator had deliberately used came back on the next commit,
  naming an interface they had never typed.

### Fixed

* **On a box with workers, a translated ICMP error was dropped as malformed.**
  The identical packet was translated correctly single-threaded. An ICMP error
  carries no L4 ports in its outer header - it is a router reporting on somebody
  else's datagram, and the datagram is embedded inside it - so the out2in handoff
  found nothing to resolve a subscriber from, counted `packets with no
  identifiable subscriber` and left the packet on whatever worker the NIC chose.
  `bpa-out2in` then searched **that** worker's session table, which was never
  going to hold a session belonging to another worker. The handoff now reads the
  embedded header, through the same parse `bpa-out2in` uses, so the worker it
  picks is by construction the worker that node searches. The consequence for an
  operator is PMTUD: a subscriber's TCP connections hang on exactly the paths
  that need a "fragmentation needed", and traceroute stops working through the
  CGNAT - on every appliance with more than one core.
* **And the counter that sent the diagnosis in the wrong direction.** A session
  that simply lived on another worker was reported as `truncated or malformed
  ICMP error`, because the bounds failures and the session lookup shared one
  return value. A miss now counts `no session for destination`, like every other
  miss; `truncated or malformed ICMP error` means what it says. A counter that
  describes the packet when the fault is in the box is worse than no counter.
* **A lab parse that was right only on one thread.** `lab/extblocks.sh` read the
  extension count off the **first** matching row of `show bpa blocks`, which with
  workers is a per-thread row. The lab failed `the subscriber filled its first
  block and got a second` while the total said the extension had happened and the
  box was behaving correctly. It reads the total line now. Not a product defect.
* **On a box with workers, every translated packet was blackholed.** `bpa-in2out`
  translated the packet, counted it as translated, and sent it to `error-drop`.
  Nothing left the box. `hexarouting-tune` configures workers on every
  appliance, so **that was the shipped configuration**. With workers the node on
  the `ip4-unicast` arc is the HANDOFF node, not the translation node: the
  feature machinery stores each chain entry's next as an index into the
  next-node list of the node that consumes it, and the translation node applied
  the handoff node's index to its own list, whose only member was `error-drop`.
  It now sends the packet to `ip4-lookup` itself when there are workers, which
  is where the chain was going — the arc is already terminated at the handoff,
  because that node enqueues to a thread and never consults the chain. With
  workers, a feature some *other* plugin enables between BPA and `ip4-lookup` is
  skipped; this product enables nothing there, and the alternative on offer was
  a dropped packet.
* **The assertion that would have caught it.** `lab/handoff.sh` is the only lab
  that runs with workers and every check in it was a **counter** — which says
  what a node decided, not what came out of the box. It passed on a box that
  dropped every packet, and did. It now sends a packet in each direction and
  asserts on the **captured packet** that it left the far interface with the
  right address. On the unfixed build four of the five new checks fail and all
  three old ones still say `ok`. Every other lab that asserts forwarding uses a
  startup file with no workers, so the forwarding path had only ever been proved
  in a configuration the product does not ship.

### Changed

* **`make test` gained `test_bpa_icmp`.** The ICMP error parse is the only place
  this plugin walks headers a remote wrote inside another packet, and its
  refusals cannot be seen from a lab: a packet dropped by a bounds check and one
  dropped after reading past the buffer look identical from outside. Every packet
  in it is placed against an unmapped page, so an over-read is a crash rather
  than a judgement call.
* **One TCP sequence-correction mechanism, not two.** `sizeof (bpa_session_t)`
  96 → 88. The FTP ALG kept its own set of correction fields beside the general
  one; a connection is either FTP or RTSP, so the duplication cost bytes rather
  than correctness — and cost the next ALG that lengthens a payload a choice
  between two mechanisms of which only one could be right. The one kept has the
  better retransmission guard: a rewritten segment arriving again, already
  accounted for, must not push a second correction, and the deleted one would
  have **doubled** the offset for exactly that case. `lab/ftp-alg.sh` asserts it
  now, on the segment after the retransmission; with the guard removed it fails
  with `unexpected: seq=1025`. FTP's own payload splice went with it, so there
  is one rewriter and one correction. The session's `alg` flag field is gone
  rather than renamed — it held one bit that was redundant with the offsets
  themselves. **The counter names changed with it**: two FTP-specific names
  became one per node describing the mechanism, and RTSP's corrections, which
  had no counter at all, are visible for the first time.

* **`show this` printed the block header and stopped, for anything typed in
  the session.** The routing half of the candidate is a flat list of verbatim
  FRR statements with no record of which block each one is in, and it arrives
  in two shapes: read back from FRR it is **indented**, and typed at the VTY it
  is not, because `Shell.feed` strips leading whitespace before dispatch. The
  block was walked by indentation, so it stopped at the first line the operator
  had typed — which is every line, in the one situation the command exists for.
  An operator who typed four statements, ran `show this` and saw only the
  header would reasonably conclude nothing was accepted. Membership in a block
  is now decided by `config.block_paths()`, which reads indentation where the
  text has it and otherwise applies the rule `do_config` itself applies —
  the same rule `frr-reload.py` uses on the very same list — and
  `route_map_findings`, `bgp_errors` and `ospf_state` were the first three
  places to learn it separately. Covered: `interface`, `router bgp` with a
  nested `address-family`, `router ospf`, `router isis`, `route-map`, `rpki`,
  `vrf` and `router static`, whose routes are shown in the block's own words
  (` route 0.0.0.0/0 10.0.0.2`) because that hierarchy is what is being edited
  and FRR has no such block at all.
* **Leaving an FRR block left no mark in the candidate.** The header went in on
  the way in and nothing recorded the way out, so the flat list said the block
  was still open — and since `frr-reload.py` decides context by statement, the
  next top-level statement was handed to FRR **inside the block just left**.
  `exit` is now staged like any other line, which is also what FRR writes
  itself; `real_routing()` ignores it, so a candidate that only sets an
  interface MTU still does not need FRR to commit.
* **A static route typed inside `vrf red` was hoisted out of the VRF.**
  `group_static()` decided what was top-level by reading column 0, which is
  right for a configuration read back from FRR and wrong for every line typed
  at the VTY: the saved file said the route was global and the operator had put
  it in a VRF, which is a different routing table.
* **`show this` inside an interface knew about two of its eleven fields.**
  `mtu` and `urpf` were listed by hand; a description, an address, a VLAN tag
  or a VRF typed a moment earlier was reported as nothing at all.
  `Config.interface_lines()` is now the single list, shared with the writer of
  the configuration file.

* **Hairpinning was broken on every appliance.** Two subscribers of the same box
  reaching each other through a public address worked only when they happened to
  land on the same worker — with N workers it failed N-1 times out of N, and the
  same pair worked or did not depending on which workers their addresses hashed
  to, with nothing in the configuration to explain it. The peer lookup ran in
  `pt->out2in`, the **per-thread** table of the worker the packet arrived on,
  which is the sender's and not the peer's. A hairpin needs two workers and
  there is no way around it: the source translation needs the sender's worker,
  for its session and its port bitmap, and the destination translation needs the
  peer's. So it is **steered** now rather than looked up — handed to
  `bpa-out2in-handoff`, which resolves the destination's owner by the same
  arithmetic every reply already uses, and `bpa-out2in` on that worker does the
  destination rewrite. That second half *is* out2in: a hairpin is an inbound
  packet whose source happens to be one of ours, and reusing it removes the
  second copy of the peer lookup that was right on one thread and wrong on every
  other. A hairpin to a port nobody is listening on is now counted by out2in as
  `no session for destination`, which on that thread is the plain truth; the
  in2out no-peer counter belongs to ESP alone, whose binding table is global for
  exactly this reason.
* **`tools/run-all-labs.sh` runs the forwarding labs twice**, once plain and once
  with workers, listed in `BOTH_WAYS`. `hexarouting-tune` gives every appliance
  with more than one core at least two workers and a smaller box everything on
  the main thread; both configurations ship and neither proves the other. #199
  and #201 were both found by a person typing `LAB_WORKERS=1`, because the suite
  had only ever run one way. Hardcoding workers in the labs was the alternative,
  and it would have moved the blind spot to the small box rather than closing it.
  `lab/icmp-tcp.sh` also asserts, on a worker run, that the handoff identified a
  subscriber for the ICMP error **and** that the error really did cross a worker
  to reach it - without the second check the first passes by luck whenever the
  session happens to belong to the arriving worker, measured. `lab/hairpin.sh`
  asserts the same kind of precondition from `show bpa session` - that the two
  subscribers really are on different workers - for the same reason: a hairpin
  between two subscribers the hash put on one worker succeeds even on the build
  where every cross-worker hairpin is dropped. #200 was found the same way, and
  `bypass`, `esp`, `extblocks`, `frag`, `ftp-alg`, `hairpin`, `icmp-tcp`, `mss`,
  `nat-behaviour`, `pptp`, `rtsp` and `two-pools` are all in the list, each
  measured both ways rather than assumed to forward something. `handoff`,
  `dynamic-workers` and `pool-del` are deliberately absent: they ask for workers
  themselves, so a second run of those would be the same run twice.
## 0.62.0 — the VTY an operator types at, ESP passthrough, and three releases with no entry

[#14](https://github.com/andrediashexa/hexarouting/issues/14),
[#190](https://github.com/andrediashexa/hexarouting/issues/190),
[#191](https://github.com/andrediashexa/hexarouting/issues/191),
[#186](https://github.com/andrediashexa/hexarouting/issues/186),
[#188](https://github.com/andrediashexa/hexarouting/issues/188),
[#192](https://github.com/andrediashexa/hexarouting/issues/192),
[#193](https://github.com/andrediashexa/hexarouting/issues/193),
[#194](https://github.com/andrediashexa/hexarouting/issues/194),
[#195](https://github.com/andrediashexa/hexarouting/issues/195),
[#196](https://github.com/andrediashexa/hexarouting/issues/196),
[#197](https://github.com/andrediashexa/hexarouting/issues/197) (partly).

### Added

* **IPsec ESP passthrough, on by default**, the fourth entry in the ALG
  registry and the first that is not an ALG: it parses nothing and rewrites
  nothing but the addresses. The SPI in particular is left alone — RFC 4303
  §3.3.2 puts it inside the integrity check, so a CGNAT that renumbered it the
  way it renumbers a port would hand the subscriber a packet its own stack
  discards while every counter reported success. Without passthrough an ESP
  packet was an unsupported protocol and the tunnel did not come up at all.
* **An inbound ESP packet is bound to a subscriber by its peer gateway**, not
  by an SPI, so the first packet of the inbound half of an SA is delivered
  rather than lost: the SPI a subscriber chose is announced over IKE, which is
  encrypted, and nothing on the wire ever tells this box what it is. Two
  subscribers sharing one public address keep separate tunnels as long as their
  peers differ; when they do not, the second is refused under its own counter
  rather than delivered on a guess, because the guess puts one customer's
  ciphertext into another customer's IPsec stack. That is the case NAT-T (RFC
  3948) exists for.
* The ESP binding table is the one structure in the plugin shared across
  workers, and `src/plugins/bpa/bpa_esp.h` says why it has to be: an inbound
  ESP packet carries no port, so the arithmetic that steers every other reply
  to the worker holding the subscriber cannot run on it.
* `lab/esp.sh` asserts all of it on the captured packet — SPI, sequence number,
  ciphertext and IP checksum — and `sh tools/lab.sh esp off` drops 21 of its 27
  assertions.
* **`show interface terse`** — one line per interface with an **Admin** column
  and a **Link** column, because those are two facts and the difference between
  them is the fault: up/down is a wire, down/down is a person, and the single
  state column a box ships with cannot say which. When the two disagree the view
  says so in words rather than leaving the conclusion to be read for. A
  sub-interface has no hardware of its own and inherits its parent's link,
  because a blank there would empty the column on exactly the interfaces an ISP
  terminates subscribers on.
* **`show interface utilization`** samples the counters and graphs what went
  past. A counter is a total since boot; a rate is a difference, so the command
  takes time and says how much **before** it starts rather than appearing to
  have hung. The elapsed time is measured, not taken from the interval that was
  asked for — on a box loaded enough to be worth asking about, a two-second
  sleep is not two seconds. Where the dataplane reports a link speed the bar is
  that interface's share of its own line rate; where it does not, the bar is
  scaled to the busiest sample in the run and the footer says so, because a
  percentage of a capacity the box does not know would be a number nobody could
  defend. A counter that goes backwards (`clear interfaces`, mid-sample) reads
  as zero and is said out loud; an interface that appeared between two samples
  is skipped rather than reported as a spike.
* **Pipe filters on any `show`** — `include`, `match`, `exclude`, `section`,
  `count`, `first`, `last`. They run over the text the command already produced,
  **after** its redactions, so `show running-config | include password` cannot
  reveal what the unfiltered command hid; that is asserted against the real
  redaction. Nothing shells out to `grep`: a pipe implemented that way is a
  shell escape wearing a filter's clothes, reachable by a read-only account on
  a product that restricts the shell to administrators on purpose. `section` is
  the one that pays most, because this box's configuration *is* blocks. Only for
  a `show` — elsewhere a `|` is a character in a regular expression or a
  description, and treating it as a filter would cut a policy in half. An
  unknown filter is refused rather than ignored, because `grep` is what somebody
  types first.
* `test/test_hexash_perms.py`: twelve assertions on the modes and the paths,
  ten of which fail against the VTY as it was, and two of which are "must not"
  guards that fail in the other direction — proved by widening the umask to
  0o000 rather than argued for.
* **CGNAT is configured under `nat`, and static routes under `router`.**
  `nat cgnat pool residential`, and a `router static` block holding `route
  <prefix> <next-hop>` lines — one `route` keyword rather than two, because the
  family is in the prefix and asking for it again is asking the operator to
  contradict themselves. Neither hierarchy exists in the tool underneath: VPP's
  plugin knows `cgnat`, and FRR has no `router static` at all — its static
  routes are top-level `ip route` statements, and a `router static` line handed
  to it is refused mid-commit. So the block is translated, at the one place
  typed configuration enters and at the one place a file is read, which is why
  the two cannot drift.
  **Both old spellings keep working**, typed and in a file, and that is not
  politeness: every box in service has a saved configuration and up to twenty
  rollback revisions written the old way, and `hexarouting.service` replays that
  file at every boot on a machine nobody is watching. A grammar that cannot read
  the box's own file leaves the box unconfigured, with nothing to say so until
  somebody notices the traffic. They are deliberately NOT offered by completion,
  because offering both teaches the one being left behind. `show
  running-config`, `show configuration` and the file `save` writes are all in
  the new hierarchy, and the box can read back what it wrote — asserted in
  `test/test_hexash_config.py` and, on a real box with a file written in the old
  format, in `lab/vty.sh`.
  A VRF's static routes stay inside their `vrf` block: FRR indents those, and
  hoisting one into `router static` would move the route to another table — a
  display transform that changes where packets go. So the column is read as well
  as the statement.
* **`start shell` is in the command tree.** It already existed and already did
  what was wanted — a child shell that returns to the VTY, refused for anything
  but an administrator — and was deliberately hidden. Hiding was never the
  boundary; the capability check is, and a command nobody can find is a command
  the maintainer has to ask for.

### Fixed

* **A non-root administrator could `commit check` a routing change and not
  `commit` it.** Applying one needs three paths that belong to FRR and not to
  this product: `/var/log/frr` (`drwxr-x--- frr:frr`, where `frr-reload` opens
  `--logfile` before it looks at `--stdout`), `/var/run/frr` (`0710 frr:frr`,
  where the delta is staged for `vtysh`), and `/etc/frr/frr.conf`
  (`0640 frr:frr`, which `frr-reload` asks `vtysh` to persist into at the end).
  The first two are scratch, so the VTY now hands `frr-reload` a private
  directory of its own for both and nothing package-owned is touched. The third
  is not scratch, so the installer makes that **file** `0660` — measured: with
  the *directory* group-writable instead, `vtysh` creates a new `frr.conf`
  owned by whoever committed and the next administrator cannot write it at all.
  `lab/vty.sh` now asserts that an administrator APPLIES the change and that
  FRR holds it, where it asserted a `commit check` and said why it could not
  ask for more.
* **`save` and the rollback store narrowed `/etc/hexarouting` past what the
  operator's group needs.** `save_running()` chmod'd the directory `0700` and
  the file `0600`, and `Rollback.push()` did the same — so the first `save` or
  `commit` by anybody, root applying the configuration at boot included, took
  away the access `install.sh` had just granted, and every later non-root
  operation on it failed. The modes now have **one owner**, `install.sh`, which
  states `2770`/`0660` group `hexa-cgnat`; the VTY neither restates nor narrows
  them, and what it has to create it creates no wider than the group and never
  world-readable. setgid, because the group alone is not enough: without it a
  revision root writes at boot is group `root` and the operator who has to roll
  back to it cannot read it.
* A revision that is present and out of reach — every box upgraded from the
  above carries up to twenty of them — is now **named** by `show rollback` and
  refused in words by `rollback`, rather than raising a Python traceback in
  front of whoever is in the maintenance window.

* **`show interface` listing nothing but `local0` now says why.** That is the
  truth and it is not an answer: it has several causes — nobody named an
  interface, the name does not resolve, the name is the interface carrying the
  default route and is refused on purpose so a remote box is not cut off, or
  DPDK is off until a reboot has reserved hugepages — and each needs different
  work. The two files that know (`/etc/vpp/startup.conf`,
  `/etc/hexarouting/tune.env`) are not where the operator is looking. Reported
  twice from a box in use and answered both times with "run these five commands
  and tell me", which is the defect. In the same words as the boot self-test,
  because two diagnoses of one fact that disagree are worse than one.
* **`show interface description` stopped denying that the box has
  descriptions.** It printed the table and then a line saying the dataplane
  carries no per-interface description — true when written, false since
  `description` began to be stored as the interface tag, and left saying so.
  Worse than never having answered, because a reader stops looking for a feature
  that exists. The assertion holding it in place asserted the sentence, so it
  went on passing; it now asserts the other direction.
* A command tree node could offer keywords **or** take a name, never both, so
  adding `terse` to `show interface` would have taken away the interface names
  it had always completed. A new option costing an old one is how a CLI gets a
  reputation.

* **`lab/lib.sh` starts FRR the way `frrinit.sh` does**, and eight labs use it.
  Six of them started zebra and sometimes bgpd and nothing else — which is not
  the FRR the product ships: on FRR 10 a static route and a prefix-list are
  configured *through* mgmtd and served by staticd, and without them `vtysh`
  answers `mgmtd is not running` to every one of them. A statement can be typed,
  accepted, reported as committed and never exist. Two labs had worked this out
  and written the reason in comments of their own while six went on starting two
  daemons, so the knowledge now has one home. Three traps recorded in it, each
  measured: mgmtd and staticd take **no `-f`** (passing one is an `invalid
  option` that leaves the daemon not running with nothing obviously wrong);
  bgpd **refuses to start** when told to load an RPKI module that is not
  installed, and that module is at `/usr/lib/*/frr/modules/`, not beside the
  daemons — the first version of this looked in the wrong place and
  `lab/bgp-policy.sh` failed seven checks, quietly, because a bgpd with no RPKI
  answers as though the feature were simply off; and the wait is on
  `mgmtd_fe.sock` rather than a `sleep`.

* **A static route whose daemon is not running is refused, instead of
  committing and forwarding nothing.** A static route is neither a `router`
  line nor a policy statement, so both existing daemon checks — the one from
  #99 and the one from #155 — walked straight past it. Nothing looked at `ip
  route`, which is the most common statement there is on an edge box. Measured
  with staticd and mgmtd stopped: `frr-reload` **exits 0 having applied
  nothing**. Its own log says `[zebra] sending configuration` / `done` for the
  line, then notices on a second pass that the statement is still missing,
  sends it *again*, and exits 0 anyway — FRR discards a statement no running
  daemon claims and says so to nobody, and the box reported `commit complete`
  for a default route that forwards no packets. Part of
  [#197](https://github.com/andrediashexa/hexarouting/issues/197).
* **A filter argument in quotes matched nothing.** `show running-config |
  section "interface Gi"` is the shape a pipe has in every shell anybody has
  used, and this filter is not a shell: nothing removed the quotes, so they
  became part of the text being searched for and the filter found nothing at
  all. Silently, which is the worst of it — an empty result reads as "there is
  no such section". A matched pair around the whole argument is taken off now;
  a quote inside the argument is still a character somebody is searching for.
* **Two `usage:` lines still taught the spelling the box moved away from** —
  `usage: cgnat interface ...` and `usage: cgnat session-table ...`. A usage
  line is an instruction, which is exactly what
  [#193](https://github.com/andrediashexa/hexarouting/issues/193) is about.
* **A refusal about a route typed in `router static` named FRR's spelling.**
  The translation happens before any check runs, so the error named a statement
  that appears nowhere in what was typed and nowhere in `show configuration`
  either: an operator who searched their own configuration for `ip route` after
  reading the error found nothing. It now reads `router static / route
  10.55.0.0/16 172.31.99.1: ...`.
* **The refusal for a missing daemon stopped naming a file the daemon is not
  in.** It said `Enable <daemon> in /etc/frr/daemons` whichever daemon was
  missing, and frrinit.sh starts zebra, mgmtd and staticd itself — they are not
  lines in that file, so an operator sent there finds nothing to enable, on a
  box whose real fault is that frr did not finish starting. `lab/routing.sh`
  asserted that `/etc/frr/daemons` appeared in the refusal, which passed
  whatever the sentence around it said, including the wrong one; it now asserts
  the advice.

### Found and not fixed

* **`frr-reload` can exit 0 having dropped a statement, and the product
  believes it.** The fix above catches the one cause that can be named in
  advance; it does not catch the next. The general answer is a read-back check
  — after applying, a statement the candidate holds that is not in FRR's
  running configuration is a failed commit whatever the exit status said, the
  same argument `show bgp summary` already makes about the FIB. What stops it
  being written today is a measured false positive: FRR omits a statement from
  its read-back when the value equals a default, so `poll-interval 60` typed
  explicitly comes back absent (recorded in `lab/igp.sh`), and refusing on that
  would be worse than the bug. The shape it needs is to report the absent
  statements without rolling back and stop printing `commit complete` when any
  are missing — with a list of what FRR round-trips that is measured rather
  than assumed
  ([#197](https://github.com/andrediashexa/hexarouting/issues/197)).
* `/etc/frr/frr.conf` is a package conffile, so an frr upgrade that reinstalls
  it puts `0640` back and non-root commits fail until `install.sh` is re-run.
  The failure is loud — the commit is refused and rolled back.

## 0.61.2 — the gate refused twice, and the second refusal was a stale assertion

### Fixed

* **`lab/install-iso.sh` asserted a self-test string the product had stopped
  printing, in the same release that stopped printing it.** It grepped the boot
  self-test for `binary API works for a non-root account` — a line the
  self-test printed after *synthesising* the group with `setpriv`. #184
  replaced that with a check on the box's real accounts, and a freshly
  installed box has no operator account at all, so it prints `[info] no
  operator account exists yet`. The lab now asserts what it can honestly see:
  the API must not be reported root-only, and a box nobody has configured yet
  must SAY it has no operator account rather than staying quiet — "no accounts"
  being mistaken for "the accounts are fine" is the whole reason that line
  exists.
* The note in 0.61.1 naming #185 as the cause of the gate's refusal of v0.61.0
  was **wrong**, and the gate refusing v0.61.1 for the identical message is
  what proved it. The #185 fix stands — it is a real defect and it was
  measured — but it was not that one. Corrected here rather than by rewriting
  0.61.1's own entry, because a changelog that edits its own history is not a
  record.

### Found and not fixed

* [#189](https://github.com/andrediashexa/hexarouting/issues/189): that a role
  granted through the VTY works on an **installed** box is still unproven. It
  needs the lab to drive the serial console; until it does, the roles feature
  is proved in a container against a VPP the lab started by hand — which is the
  shape [#99](https://github.com/andrediashexa/hexarouting/issues/99),
  [#168](https://github.com/andrediashexa/hexarouting/issues/168) and
  [#182](https://github.com/andrediashexa/hexarouting/issues/182) all had.

## 0.61.1 — a root API client reset the segment, and the release gate was right to refuse

[#185](https://github.com/andrediashexa/hexarouting/issues/185).

### Fixed

* **One client running as root reset `/dev/shm/global_vm` to `root:root`, and
  every non-root client afterwards died inside `svm_region_map`** — `vat2` with
  SIGSEGV, which reads like a client bug rather than a permission one. On a
  shipped box `hexarouting.service` applies the saved configuration as root at
  every boot, so the drop-in that sets the group when VPP starts was undone by
  the very next unit. Repaired by the thing that breaks it rather than by
  another unit chasing it, because an interactive root session breaks it too
  and no unit runs after that one. Best effort and silent: a box where the
  group does not exist, or where this is not root, is not a box this can fix,
  and failing a commit over it would refuse a change that worked.
* This was found by 0.61.0's own work and written down there as *not* fixed. It
  became a blocker in the next release because of the other half of that work
  — the boot self-test stopped synthesising the group with `setpriv`. The old
  check asked "would an account in group `vpp` work", which is true; the new
  one asks "is any account reaching it", which on a real installed box was
  false. The first self-test that looked at the real accounts found it on the
  first try, which is the whole argument for that change.

## 0.61.0 — roles that were unreachable, OSPF the VTY understands, and a stall only this box can see

[#173](https://github.com/andrediashexa/hexarouting/issues/173),
[#174](https://github.com/andrediashexa/hexarouting/issues/174),
[#183](https://github.com/andrediashexa/hexarouting/issues/183),
[#184](https://github.com/andrediashexa/hexarouting/issues/184).

### Added

* **`router ospf` is a block the VTY understands**, at both levels. It used to
  be passed through and understood by nobody: no completion, no `?`, and a typo
  found when FRR refused it mid-commit. The router-level and interface-level
  statements an ISP edge or aggregation box uses are modelled — not everything
  FRR takes, because a grammar listing a thousand commands is the reference
  manual this VTY exists not to be.
* **The OSPF MTU stall, which FRR cannot report at all.** FRR compares the
  TAP's MTU in its database description packets while the wire runs at the
  dataplane's, so when they differ the adjacency sits in Exchange for ever with
  no log line on either side. `docs/routing.md` had called that Pitfall 1 since
  before there was a check for it. Reported with BOTH numbers, because "the
  MTUs disagree" is not actionable and "1400 against 1500" is — and as a
  warning, never a refusal, because `ip ospf mtu-ignore` is the supported
  escape hatch and a deliberate asymmetry is legitimate.
* Four more OSPF findings reported and one refused. Refused: a
  `passive-interface` naming an interface the box does not have, which commits
  and leaves the port the operator meant to keep quiet talking. Reported: a
  `network` statement matching no address on this box; an area border router
  with interfaces in two areas and none in area 0, so inter-area routes never
  propagate; `redistribute bgp` with no route-map, which on an edge box is the
  whole table into the IGP; and a **subscriber-facing port speaking OSPF** —
  the cross-half check only this product can make, because FRR does not know
  what a CGNAT pool is and the dataplane does not know what OSPF is.
* **`lab/igp.sh`**: both IGPs configured through the VTY and asserted on the
  FIB, with `ospfd.conf` and `isisd.conf` emptied to a hostname line the way
  `bgpd.conf` was for #142, so every assertion is evidence about the VTY rather
  than about a file the lab wrote. The MTU stall is measured rather than
  papered over — 40 s with the prefix absent, then `mtu 1500` through the VTY
  and Full in 6 s, asserted against the same adjacency's own convergence rather
  than a wall-clock threshold. First IPv6 route in this suite proved to have
  reached the dataplane by any protocol.

### Fixed

* **Attaching CGNAT over the binary API skipped the reassembly the CLI path
  enables.** Since #76 moved the VTY off `vppctl`, an interface attached by the
  product got no shallow virtual reassembly and no tcp-mss recompute — both
  silent. On an API-attached interface a following fragment was not recognised
  as one: it was read with whatever bytes sat where the ports would be, opened
  a SECOND session, burned a port of the block, and the datagram left in two
  halves wearing two different source ports; out2in the same fragment matched
  nothing and was dropped. One shared function now, not two copies, because a
  third caller is not hypothetical. The detach mirror was worse than it looked
  — the API neither incremented nor decremented, so a CLI attach followed by an
  API detach decremented a refcount the API had never raised; all four
  combinations close at zero and the lab asserts the round trip. The MSS half
  was a bounded window rather than a permanent state: the expiry process
  recomputes once a second, so the missing call left the clamp up to 1 s stale
  rather than wrong for ever.

* **The VTY created every account in no group, so every role was
  unreachable.** `apply_users()` ran `useradd`, `usermod -s` and `chpasswd`
  and no group command at all, and the `user` block had no way to say what an
  account may do. So `capabilities()` came back empty for every account the
  product made: read-only for ever, unable to open the dataplane socket and
  unable to run `vtysh`, with no product command to grant a role. The block
  now takes `role administrator` or `role cgnat-operator`, `commit` writes the
  memberships the role is made of — `hexa-admin`/`hexa-cgnat`, plus `vpp`,
  `frr` and `frrvty`, which every account it owns needs before authorization
  is even the question — and `no role` takes them away again. Only those five
  are touched; a group an operator granted for their own reasons stays.
* **The role is read back from the group database, not from the file.**
  A membership removed by hand shows up in `show running-config` instead of
  the VTY going on reporting a rank the box no longer grants.
* **A commit that removes the last administrator is refused**, the way one
  that leaves nobody able to log in over SSH already was, and for the same
  reason: a CGNAT operator cannot grant a role, so the way back is a root
  shell the operator may not have. `root` takes no role and its memberships
  are never touched.
* **The boot self-test stopped synthesising the group it was checking for.**
  It proved reach with `setpriv --groups vpp`, which answers "would an account
  in group `vpp` work" and never "is any account in group `vpp`" — so it was
  green throughout the life of the defect above. It now reports an account
  that holds a role and not the memberships, and makes the call as an account
  the box really has.
* **`lab/vty.sh` stopped configuring the box under test.** It granted every
  membership with `usermod`, so the roles lab proved a box nobody received.
  The accounts are created through the VTY, and the negative half is asserted
  with them: an account given no role is in neither role group and can do
  nothing but read.

### Found and not fixed

* `ospf6d` is enabled nowhere, so the VTY models an OSPFv3 block the box cannot
  run ([#187](https://github.com/andrediashexa/hexarouting/issues/187) — the
  maintainer's call whether to ship the daemon or withdraw the grammar).
* `frr-reload.py` logs to `/var/log/frr/frr-reload.log`, which the frr package
  ships `drwxr-x--- frr:frr`, so a non-root administrator can validate a
  routing change and cannot apply one
  ([#186](https://github.com/andrediashexa/hexarouting/issues/186) — fixed
  later, and it turned out to be three paths rather than this one).

## 0.60.0 — the second lab-only step, a trail that missed a change, and a diagnostic that lied

[#168](https://github.com/andrediashexa/hexarouting/issues/168),
[#124](https://github.com/andrediashexa/hexarouting/issues/124),
[#74](https://github.com/andrediashexa/hexarouting/issues/74), and an audit of
every lab for the class those first two belong to.

### Fixed

* **IS-IS committed cleanly and never formed an adjacency.** `lcp osi-proto
  enable 0x83` existed in one lab and in the documentation — as a **MUST** — and
  nowhere in the product. On a shipped box the commit succeeded, `isisd` came
  up, the running configuration read back correctly, and no adjacency ever
  formed, with the only evidence in a dataplane error counter nothing surfaced.

  The product enables it now, over the binary API, when the configuration
  contains an IS-IS instance. Not left to the operator: it is not a choice
  anybody would make differently.

  Reading it back took two attempts. VPP answers `lcp_osi_proto_get` with a
  **hex string** — `"0x83"` for one protocol, bare `"0x"` for none — not a list
  of numbers. Read as a list it came back empty every time, so the reader said
  "nothing is punted" on a box that was punting, and the lab assertion counting
  `131` found nothing while the adjacency was up.

* **Attaching CGNAT to an interface was not in the audit trail.** Three changes
  applied to a box — attach via the VTY, detach via the VTY, attach via
  `vppctl` — grew the trail by **zero lines**. The pool messages record because
  every mutator ends in the audit call; the interface attach never entered that
  file at all, and the CLI twin had the same hole.

  One writer now, so a new entry kind cannot skip a link or reorder fields.
  Every integrity property survives, and the verify path needed no change.

* **`show bpa subscriber` denied state the session table was holding.** Two
  tenants sharing an inside prefix, told apart by a VRF: five live sessions, and
  the command answered *"no live state: this subscriber holds no block and has
  no sessions on this box"* — while naming the **other tenant's** public
  address, which is the kind of answer a court order gets.

  One lookup was answering two questions it cannot carry, and the pool lookup
  disagreed with the datapath's: the command asked for "any VRF" and took the
  first pool claiming the address, while `in2out` asks with the packet's
  `fib_index`. The false sentence is split into the two answers it was
  collapsing, and nothing was added for the cases that were already right.

### Internal

* **Four labs were testing a weaker box than the one shipped.** `lab/vty.sh`
  made `/run/vpp` world-writable, `/etc/frr` world-readable, and the saved
  configuration `0644` — which the installer installs `0660` group
  `hexa-cgnat` with a comment saying *never world-readable*, because it holds
  every password hash. So the lab handed the read-only account a copy of what
  the product deliberately withholds.

  The chmods were there because `lab/startup-lcp.conf` lacked the `gid vpp` the
  appliance carries, so the lab's sockets came out `root:root`. With the gid
  added, group membership does the work and the results are unchanged — which
  is the proof the chmods were hiding something rather than enabling something.

  `lab/api.sh` pointed `vat2` at the build tree for all five calls: the one lab
  that drives the wire format was proving it against a client layout no
  appliance runs.

### Found and filed, not fixed

The lab audit found four gaps the product genuinely has. The worst is
[#184](https://github.com/andrediashexa/hexarouting/issues/184): **the VTY
creates accounts and puts them in no group**, so every account it makes is
read-only for ever, cannot open the dataplane socket, and cannot run `vtysh`.
The roles are not loose — they are unreachable, and `lab/vty.sh` granted the
memberships by hand.

Also [#183](https://github.com/andrediashexa/hexarouting/issues/183): attaching
CGNAT over the binary API skips the reassembly enable and the MSS recompute that
the CLI path does — so since the VTY moved off `vppctl`, an interface attached
by the product drops fragments.

## 0.59.0 — a loopback, the pair nothing was creating, and a flag that could not be sent

[#179](https://github.com/andrediashexa/hexarouting/issues/179),
[#182](https://github.com/andrediashexa/hexarouting/issues/182) in part,
[#136](https://github.com/andrediashexa/hexarouting/issues/136), and
[#151](https://github.com/andrediashexa/hexarouting/issues/151) in part.

### Fixed

* **Nothing in the product created a `linux_cp` pair.** `lcp create` appeared in
  five labs and nowhere else. The `linux-cp` settings in the startup
  configuration govern how an **existing** pair behaves; `lcp-auto-subint`
  creates a pair for a sub-interface of an interface that is *already* paired.
  Neither creates the first one.

  So on a box installed from the ISO, FRR saw no interface for the port that
  carries traffic and **BGP, OSPF and IS-IS were configurable and inert** —
  while the labs created the pair by hand, and therefore proved a box nobody
  received. That is the third instance of the shape, after the routing daemons
  ([#99](https://github.com/andrediashexa/hexarouting/issues/99)) and OSI
  passthrough ([#168](https://github.com/andrediashexa/hexarouting/issues/168)).

  `linux-name <name>` is what the kernel, `ip` and FRR see an interface by,
  created over the binary API. TAP rather than TUN, and not by default: IS-IS is
  OSI straight over the link and a TUN pair has no link layer to carry it.

* **There was no way to create a loopback**, and the VTY already **refused**
  `neighbor X update-source Loopback0` for not existing — so the box named what
  was missing and gave no way to make it, which is worse than not offering the
  statement. Naming a loopback creates it, the way naming a sub-interface does.
  `loop<n>` is VPP's own spelling, and the instance in the name is the instance
  VPP is given: `loop3` produces `loop3`, not whatever was next free.

* **`vat2` segfaults on a flag it cannot send, and the client called it a dead
  dataplane.** Reproduced against VPP 26.06: a bare flag name works; an integer,
  `0`, `null` and a JSON list — including a one-element list — all segfault. The
  comma, pipe and space spellings do **not** segfault, because they are strings
  and take the clean-refusal path.

  The cause is upstream: `interface_types.api.json` declares `sub_if_flags` and
  `if_status_flags` under `enums` rather than `enumflags`, so the generated
  `_fromjson` does `strcmp` on the NULL a non-string returns. A type declared
  `enumflags` generates a loop over a JSON array. Multi-bit values **are**
  expressible in vat2's JSON; these two are declared with the wrong keyword.

  And both failures were classified as "no reply from the dataplane; check
  `systemctl status vpp`" — for a dataplane that was answering perfectly. The
  client now reports `CANNOT_SEND` and refuses a value whose *shape* vat2 dies
  on, before the process starts, checked on shape rather than against a list of
  member names so it cannot drift out of step with VPP.

### Measured, and it changed the decision

`EXACT_MATCH` buys **nothing observable** here. With the packet generator, a
three-tag frame lands on a sub-interface created *with* the flag by `vppctl`
and on one created with `TWO_TAGS` alone. `eth_input_tag_lookup()` — the path a
sub-interface on a port takes — sets `n_tags = 2` whenever the second ethertype
is `0x8100` and never asks for a three-tag match. The control proves exact
matching works at all: a single-tag `exact-match` sub-interface refuses the 2-
and 3-tag frames.

So the acceptance test #136 proposed — a three-tag frame does not land — cannot
pass on this VPP even with the flag sent correctly. What ships instead is a
warning on every QinQ creation naming what the interface accepts, and
`lab/subif.sh` asserts the three-tag frame **lands**. Asserting the wrong
behaviour on purpose: a VPP that ever fixes this fails the lab.

### Internal

* **Six assertions that could not fail**, each demonstrated in three states: the
  thing broken with the old assertion still passing, the new one failing on that
  same build, and both restored.

  The worst was `lab/frag.sh`. `expect_not_in "it did not open a phantom
  session" "12346 -> "` looked for a needle the product never prints, because
  the lab only ever sends from 12345. Disabling the reassembly the lab exists to
  test made the box open a real phantom session — and the assertion stayed
  green.

  The other five were one family: a label standing in for a value.
  `port-holddown` matches whatever the timer is set to; `packets translated` is
  declared for **both** directions, so removing the return path still matched;
  `allocations` is a column heading printed for a pool with zero.

* One existing assertion was fragile by construction and had to change:
  "shutdown is in the running configuration, for THIS interface" counted four
  lines back from the statement, which held until a statement was added to the
  block between the two. It reads the block now.

## 0.58.0 — a secret nobody covered, an installer that checks its work, and the suite's floor

[#181](https://github.com/andrediashexa/hexarouting/issues/181),
[#83](https://github.com/andrediashexa/hexarouting/issues/83) in part, and
[#167](https://github.com/andrediashexa/hexarouting/issues/167).

### Fixed

* **FRR key chains printed their secret in the clear.** `key chain` is the other
  way OSPF, IS-IS, RIP and BFD carry an authentication secret, and
  `ip rip authentication string` is the same class.

  The syntax was verified against a running FRR 10.7.1 rather than remembered:
  `key-string` takes a single token — a multi-word attempt is rejected and never
  reaches the running configuration — and the lines around it are **not**
  secrets. `key chain <name>` is a name, `key 1` an index,
  `cryptographic-algorithm` an algorithm, and both `ip rip authentication
  key-chain` and `ip ospf authentication key-chain` are references. All of those
  survive untouched.

### Added

* **The installer verifies the disk it just wrote**, while the target is still
  mounted and **before** the success line: GRUB has an entry whose kernel is on
  the disk, every UUID in the target's `fstab` resolves, and `root` has a real
  password hash rather than the live image's locked one. An `fstab` naming no
  UUID at all is a failure, not a vacuous pass. A failure is fatal and the
  message tells the operator to leave the medium in place — a box that reports
  success and boots to a `grub>` prompt is a site visit.

* **Answers can come from a file**, so the unattended path stops publishing the
  root password on the kernel command line. The password is never printed — not
  in the summary, not in an error — and a malformed line is reported by line
  **number** only, never quoted back, because the line that failed to parse may
  be the password line.

  The mirrored install is deliberately not done: its acceptance criterion is a
  lab that removes one disk, and doing it on assumptions is what the issue's own
  scope note warns against.

### Internal

* **`lab/routing.sh` was the suite's floor; it is split.** Six subjects in one
  file, 507 s measured alone, and a per-lab cap raised twice in one day because
  it kept being killed.

  The seam was verified by `grep` rather than by reading: in the moved half
  there is exactly **one** reference to OSPF or IS-IS — a `router ospf` inside a
  `commit check` that tests wording, not an adjacency — and **zero** to the two
  IGP prefixes. Each file now builds only what it uses.

  **507 s → 304 s and 243 s.** 158 checks → 61 + 98, with the descriptions
  diffed to prove nothing was lost or duplicated. The one added check exists so
  an assertion cannot become vacuous: the graceful-shutdown section reads a
  prefix arriving from the third AS one BGP hop back, which in the single file
  had long arrived by the time that section ran ten minutes in, and now runs two
  minutes in. The precondition is explicit rather than inherited from how long
  the file above happened to take.

  Cap 1200 → 900. Not 600 — that is the number that killed a 329 s lab under
  `LAB_JOBS=32`.

* **`lab/pool-del.sh` asserted a wall-clock threshold and measured the host.**
  It passed alone and failed in the suite. The load-bearing assertion is now a
  **ratio** — the cost of one barrier acquisition must not follow the size of
  the table — because a barrier waits for every worker to arrive, and under
  sixteen-way contention an acquisition was measured at 1 008 443 µs, a hair
  under VPP's own barrier timeout, for reasons that have nothing to do with what
  is inside it. That noise adds to both measurements, so the ratio survives it.
  The absolute bound that remains is loose and labelled as a ceiling rather than
  a measurement.

## 0.57.0 — deleting a pool no longer stops forwarding for everybody

[#171](https://github.com/andrediashexa/hexarouting/issues/171).

### Fixed

* **`bpa_pool_del` held the worker barrier across an unbounded walk of every
  session on every worker.** Forwarding was stopped for all subscribers while
  it ran, and an ordinary `commit` reaches it — `hexash` replaces any pool whose
  configuration text differs, so a change to an ALG or an MSS clamp during an
  incident tears the pool down.

  Measured with VPP's own barrier accounting — the `t` field of the `bar-rel`
  events, which is how long forwarding was actually stopped — on two workers,
  with subscriber traffic running through the pool being deleted:

  | sessions | before | after |
  |---|---|---|
  | 10 000 | **5 964 µs**, one acquisition | **1 451 µs**, 9 acquisitions over 11 ms |
  | ~49 000 | **37 095 µs**, one acquisition | **1 374 µs**, 28 acquisitions over 45 ms |

  4.9× the sessions, 6.2× the stall: a straight line at ~0.6 µs per session
  freed, which puts the design target of 200k subscribers into the hundreds of
  milliseconds. Flat afterwards.

### How, and why not the other way

The delete stays **synchronous** — it returns only when the last session is
gone — and only the *walk* is chunked: one short barrier to detach, budgeted
chunks with the barrier open in between, one short barrier to free the
definition.

Returning early and letting the expiry pass reclaim would have been simpler and
is wrong, because of #69: `hexash` replaces a changed pool with `del` then `add`
of the same prefixes. A delete that returns while sessions drain lets the
replacement pool compute the same blocks and hand subscriber 5 the same public
port the old pool's subscriber 5 is still holding — both write the same
`out2in` key, the second insert wins, and freeing the old session deletes the
new subscriber's entry.

**Two costs worth stating.** The entry points had to be declared mp-safe, or
their dispatchers hold the barrier around the whole call and a nested release is
a no-op — VPP says so in its own event log — which would have left the walk
budgeted and the outage exactly as long. And the main thread is now blocked for
the delete's wall time rather than the barrier being held for it: no expiry, no
API, for ~11 ms at 10k sessions. That is better than an outage of the same
length.

Dropping the barrier mid-walk lets the datapath run against a half-drained pool,
so three gates make one forward pass sufficient: `bpa_session_bind` refuses a
deleted pool, without which an ALG opening a data connection on a control
session the cursor has passed would create a session *behind* the cursor;
`bpa_pool_by_inside` skips deleted pools, so in2out reports "no pool" rather
than being counted as an exhausted port block — the counter an operator chases
when a customer complains; and `bpa_pool_by_outside` deliberately does **not**
skip them, because sessions not yet freed must keep answering.

### Internal

* `lab/pool-del.sh` is new and **fails on the unfixed build**, verified by
  reverting and reapplying the source diff. It runs two workers, because with
  fewer than two VPP's barrier returns without doing anything and a
  single-threaded lab would report zero on a broken build. It asserts the
  mechanism as well as the number: the delete must take more than one barrier of
  its own.

* A correction to the issue's own text: `bpa_static_install` and
  `bpa_static_uninstall` do **not** walk every session table. Each is a fixed
  number of lookups per thread plus one subscriber's own blocks. That claim came
  from a reading rather than a measurement, and it was wrong.

## 0.56.0 — secrets that were printed, and a victim that was not protected

Two shipped-box defects
([#169](https://github.com/andrediashexa/hexarouting/issues/169),
[#172](https://github.com/andrediashexa/hexarouting/issues/172) in part).

### Fixed

* **OSPF and IS-IS authentication keys are no longer printed in the clear.**
  `redact()` covered an account's `password` line and a BGP neighbour's, and
  stopped there. Five IGP spellings carry a secret and all five printed in full,
  in `show running-config` **and** in `show this`:

  ```
   ip ospf authentication-key <key>
   ip ospf message-digest-key 1 md5 <key>
   isis password md5 <key>
   domain-password md5 <key>
   area-password md5 <key>
  ```

  These are not hashes. Each opens the adjacency on the **other** router as
  well.

  Matched by keyword prefix with the secret's position named, rather than by
  looking for the word "password" anywhere — a rule that loose would mangle a
  description that mentions it. Only the secret is replaced: the key id, the
  `md5`, a trailing source address and IS-IS's `authenticate snp validate` all
  survive, so the operator can still see which key is set and how it is used.

  Still a display transform: `Config.text()` is byte-identical, so a rollback
  can still restore an adjacency. Seventeen checks, and reverting the fix alone
  makes fifteen of them fail — they are not vacuous.

* **VPP is no longer the kernel's likely OOM victim.** This project watched the
  kernel kill VPP while VPP held **19 MB** resident — *"it was not the glutton;
  it was the chosen victim"*. The fix applied at the time was to require 4 GB,
  which raises the threshold and does not change who gets picked.

  A drop-in sets `OOMScoreAdjust=-1000` on `vpp.service`, and
  `hexarouting.service` gets `MemoryMax=512M` — derived, not invented: the docs
  state a 4 GB floor, the tuner already reserves `max(2, total/4)` GB outside
  hugepages, and the measured peak RSS of the VTY and its modules is 13 MB. So
  512M is a quarter of the reserve and forty times the observed need, and the
  working set does not grow with the routing table or the session count because
  both live in VPP.

  **Deliberately not bounded**: `frr.service`, because nothing in this
  repository says what `bgpd` costs with a full table and a bound guessed low
  kills BGP rather than degrading it; the tuner, because an OOM kill during
  `update-grub` leaves `isolcpus` and `startup.conf` disagreeing; and the
  self-test, which has to run precisely when memory is tight.

* **`--max-workers` was computed by the tuner and never passed.** On a 32-core
  socket that leaves thirty-one cores spinning as workers and **one** physical
  core for FRR, `hexash`, `sshd` and VPP's main thread — which is the
  precondition for the main thread being preempted while it holds the worker
  barrier. `HEXA_MAX_WORKERS` wires it through, empty by default, so a box
  booting this build gets exactly what it gets today. A non-numeric value is
  reported and ignored rather than failing the tuning service, because argparse
  exiting non-zero would leave the kernel command line at whatever the previous
  boot wrote.

### Unverified, and marked as such

`OOMScoreAdjust=-1000` was confirmed to parse but not observed taking effect: an
unprivileged user unit may only raise its own score, and `vpp.service` runs as
root under the system manager. That is reasoning, not a measurement. No live
OOM experiment was run.

The remaining half of #172 — giving VPP's main thread a core of its own — needs
hardware this machine is not, and is untouched.

## 0.55.0 — origin validation, and a refusal that was wrong

RPKI ([#147](https://github.com/andrediashexa/hexarouting/issues/147)) and a
regression shipped in v0.51.0
([#178](https://github.com/andrediashexa/hexarouting/issues/178)).

### Added

* **Origin validation.** The `rpki` block, `match rpki valid|invalid|notfound`
  in a route-map, and `show rpki` answered by FRR. The appliance installs
  `frr-rpki-rtrlib` and `/etc/frr/daemons` carries `-M rpki`.

  RPKI is a **module**, and `bgpd --version` reports `--enable-rpki` whether or
  not it is present. Without it every RPKI command answers `bgpd is not
  running`, because bgpd refuses to start when told to load a module it cannot
  find — a symptom that points at the wrong thing, which is why the boot
  self-test now checks for the module by name.

* **`commit` reports a policy that is not validating anything:**

  ```
  warning: match rpki notfound: no RPKI cache is configured, so every route is
  notfound and this clause matches all of them
  ```

  Reported, not refused: an operator may be building the policy before the
  cache, on purpose. This is the failure origin validation *creates* — a
  validator that is not connected eventually marks everything `notfound`, and a
  policy keyed on it then acts on every route.

* **`show bgp summary` and the boot self-test say when validation is not
  available**, and say it accurately. Measured: `show rpki cache-connection`
  prints a header line `Connected to group 1` that stays for as long as the
  cache is configured — seventy seconds after the server was killed it still
  said Connected. The real state is the per-cache `(connected)` marker, which
  disappears within a second. Everything here reads the marker.

  Also measured: the prefix table is **not** emptied when the cache goes.
  Validation runs on what was already learned until `expire_interval` passes.
  So the box says so, rather than "nothing is validated", which would send an
  operator looking for an outage that has not started.

### Fixed

* **An OSPF NBMA `neighbor` was refused with a BGP error.** `bgp_errors()`
  scanned every routing line for the word `neighbor`, and OSPF uses it too:

  ```
  error: neighbor 10.0.0.2 poll-interval 60: 10.0.0.2 has no 'remote-as' ...
  ```

  on a box that need not have BGP configured at all. The check is now
  block-aware, tracking context **by the statement rather than by indentation**
  — the same defect `route_map_findings` had already been caught with once.

* **An AS-path regex read back from the box was never checked, and every match
  count was taken against a pattern nobody wrote.** FRR returns the statement
  with a `seq` it assigned — `bgp as-path access-list RX1 seq 5 permit ^65001$`
  — and a parser counting words from the left read `5 permit ^65001$` as the
  regular expression. Both now find the name and the regex by keyword. The
  cross-check against FRR did not catch this because it feeds the statement in
  the form an operator *types*, which has no `seq`.

### Internal

* `HEXA_FRR_DUMP` captures exactly what was sent to `frr-reload` and exactly
  what it said. Kept, because `_summarise()` necessarily throws away most of
  that output and twice the discarded line was the one naming the cause — a
  stale mgmtd refusal, and an `rpki cache` missing its transport keyword, which
  read like an upstream limitation rather than a missing word.

## 0.54.0 — the BGP commands an operator runs while something is wrong

Operating a session, not just configuring one
([#148](https://github.com/andrediashexa/hexarouting/issues/148)), and a
removal that added a line
([#166](https://github.com/andrediashexa/hexarouting/issues/166)).

### Added

* **`show bgp summary`, answered against the table that forwards.**

  ```
    Neighbour          AS       State          Prefixes  Up
    10.0.0.2           65001    Established    2         00:01:55
    10.1.0.2           65001    Established    2         00:03:37

    2 session(s), 2 established
    all 3 BGP prefix(es) are in the forwarding table
  ```

  FRR's summary says a session is Established and carrying N prefixes, which is
  a real answer to a different question: a prefix FRR holds that VPP never got
  moves no packets. The count is taken for the whole table rather than per
  session, deliberately — the FIB does not record which session a route arrived
  on, and a per-neighbour number could not be defended.

* **`clear bgp <neighbour> [soft [in|out]]`**, admin-only. Operational, and it
  changes nothing on disk. Until now it meant leaving `hexash` for `vtysh` on a
  box whose point is that the operator does not have to.

* **Routes the box is not keeping are said, not shown as empty.**
  `received-routes` answers from what the neighbour sent before inbound policy,
  which the box only has with `soft-reconfiguration inbound`. Without it FRR
  prints an empty table, and "no routes received" and "I am not keeping them"
  are different answers during an incident. `clear ... soft in` refuses for the
  same reason and names the alternative that always works.

* **`bgp graceful-shutdown`** (RFC 8326), tested by its effect: the neighbour
  receives the well-known community, the local preference of received routes
  drops to `0`, and undoing it takes both back off.

### Fixed

* **`no <routing statement>` removes the statement instead of adding a line.**
  It was staged in the candidate as typed, so the candidate held both `ip route
  203.0.114.0/24 blackhole` and `no ip route 203.0.114.0/24 blackhole` — which
  is not a configuration. FRR answered the only way it could:

  ```
  % Can't find static route specified
  ```

  `frr-reload` had already removed the route from the delta, then reached the
  literal `no` line and found nothing to remove. A `no` with nothing to remove
  is still staged as written, because some `no` forms **are** configuration —
  `no bgp ebgp-requires-policy` is the one every eBGP session in the lab needs,
  and that case now has its own assertion.

### Internal

* The announce/withdraw pair is asserted in the **neighbour's** table and in
  the **VPP FIB**, both directions. FRR holding a route is a different claim
  from the box forwarding on it.

* The lab suite runs 16 at a time. Measured on 36 labs: 8 cores gave 1568 s at
  4 and 1334 s at 8; 32 cores gave 940 s at 16 with peak load 3.09, and 826 s
  at 32 — but at 32 `routing` hit the per-lab cap and did not run, because that
  many containers each starting VPP make the slowest lab slower. 12% of wall
  clock is not worth a lab that did not run. Load 3.09 of 32 says the rest is
  waiting rather than computing, and the floor is now `lab/routing.sh` alone
  ([#167](https://github.com/andrediashexa/hexarouting/issues/167)).

* The graceful-shutdown community is asserted by the name FRR renders,
  `gracefulShutdown`. The raw `65535:0` never appears in its output, so
  asserting the number would have been asserting a rendering nothing produces.

## 0.53.0 — a regex that compiles is not a regex that is right

AS-path and community lists
([#146](https://github.com/andrediashexa/hexarouting/issues/146)).

### Added

* **An AS-path regex that does not compile is refused at the prompt**, not
  mid-commit, which is where FRR compiles it. Ten cases go to both the VTY and
  a running FRR in the lab and must agree — the VTY now holds an opinion about
  what FRR accepts, and an opinion that drifts refuses configurations that are
  fine.

* **What a regex actually matches, counted against the paths the box has
  received.**

  ```
  note: AS-LOOSE '65002' matches 2 of 5 received AS path(s); e.g. '65001 65002'
  ```

  This is the failure that cannot be refused: `permit 65001` is legal, compiles,
  and also matches `165001` and `650012`, because a missing `^` or `$` turns an
  exact match into a substring one. FRR's `_` means *any boundary*, not an
  underscore. Nobody sees this by reading their own regex back.

  Read from JSON, not from the human table, where a long AS path wraps onto the
  next line — losing exactly the long paths a regex is most likely to get wrong.
  With no paths received, the VTY says so rather than reporting zero: "matches
  0" and "there was nothing to match against" are different answers.

* **A list a route-map names and nothing defines is refused.** It commits, the
  policy reads correctly, and the clause matches nothing forever. Both spellings
  of a community list count as defining it.

* **`bgp as-path access-list`, `bgp community-list` and
  `bgp large-community-list` complete**, and so do their names wherever a
  route-map matches on them.

### Internal

* The lab runs a **third autonomous system** one hop behind the peer, so the
  box receives two different origins. A policy accepts only what `65002`
  originated: its prefix must stay in the VPP FIB and the other must leave it.
  The second half is the one that matters — a regex matching everything would
  keep both, and a test that only looked at the route the policy meant to
  select would pass on it.

* The lab suite runs 8 at a time rather than 4, measured: 1568 s against
  1334 s on the same 36 labs, with the faster run competing against an ISO
  build. Doubling the parallelism buys 15%, which says the rest of the wall
  clock is one lab waiting — `routing` alone runs over 350 s, and the suite
  cannot finish before its slowest member. More cores will not change that.

* The router-advertisement suppression test was measured wrongly and passed
  wrongly. Its capture opened before the commit — which is right for the
  *enable* direction, because VPP sends its initial advertisements as
  advertising starts — and therefore counted an advertisement already in flight
  when suppression was applied. It now reads VPP's own
  `n_advertisements_sent` across the window, which is not a matter of timing.

## 0.52.0 — route-maps, and a refusal the next commit used to pay for

Policy the VTY understands
([#144](https://github.com/andrediashexa/hexarouting/issues/144)), and the half
[#160](https://github.com/andrediashexa/hexarouting/issues/160) left open
([#164](https://github.com/andrediashexa/hexarouting/issues/164)).

### Added

* **`match` and `set` complete inside a route-map**, with the clause set an ISP
  edge uses, and names read back from FRR: prefix-lists, AS-path lists,
  community lists, route-maps and neighbours.

* **A `deny` clause carrying `set` is refused.** The set never runs — the route
  is denied — so the operator wrote something that reads like policy and is not.

* **A `permit` clause with no `match` is reported, not refused.** It permits
  every route, which is a deliberate way to end a policy and is also exactly
  what an unfinished one looks like. The VTY cannot tell them apart and neither
  can anyone afterwards, so it says so at `commit`. A clause an earlier
  catch-all makes unreachable is reported the same way; sequence numbers are the
  operator's and are never renumbered.

* **What was not checked is marked** where the configuration is displayed:

  ```
  set tag 100    <- not checked by this VTY
  ```

  Display only. The marker never reaches the saved configuration, a rollback
  revision or the file handed to FRR — and it is stripped on the way back in,
  so copying the screen into a file is safe. Both halves are asserted.

### Fixed

* **A stale FRR refusal no longer costs the operator's next commit.** Measured:
  a statement FRR rejects through the file-load path — which is the path
  `frr-reload` uses — leaves the rejected entry in mgmtd's candidate datastore,
  and the *next* commit is refused with the same message, naming a statement
  that is in no configuration the operator can find. Exactly one commit, then it
  clears. The VTY now takes that one itself.

  Which absorbers work was measured, not guessed: `configure terminal`+`end`
  does not clear it, nor does setting the hostname to what it already is —
  neither changes anything, so mgmtd never re-validates. `no ip prefix-list` on
  a name nobody has does, and leaves nothing behind.

### Internal

* `lab/routing.sh` has a **second link to the same peer**, so both sessions
  carry the same prefix and `set local-preference` has something to choose
  between. The assertion is on the VPP FIB's next hop and on the fact that it
  changed; which path the tie-break picked first is read at runtime, so the test
  is about the policy rather than about FRR's tie-break.

* The prepend test reads the **neighbour's** table, and the second session
  carries the same prefix with a `match` that matches nothing — same prefix,
  same box, same moment, policy the only difference. Without that half, a `set`
  applying to everything would pass a test that only checked the session it was
  meant to change.

* Three assertions were removed or rewritten after they turned out not to be
  able to fail: `set extcommunity rt nonsense` is *accepted* by FRR, so a test
  built on it proving "the next commit is unharmed" proved nothing. The
  absorber is now tested against a poison that is reproduced first, with the
  control — "with the refusal standing, the next commit MUST fail" — asserted
  before the fix is asserted.

## 0.51.0 — BGP neighbours the VTY understands

The `router bgp` block was passed through and understood word for word by
nobody ([#142](https://github.com/andrediashexa/hexarouting/issues/142)).

### Added

* **Completion and `?` inside `router bgp` and inside an address family**, with
  two grammars rather than one: `neighbor X route-map RM in` belongs to an
  address family and does nothing outside one, while `neighbor X remote-as` is
  what creates the session. `remote-as` is deliberately not offered inside an
  address family.

* **The words after a neighbour's address complete.** They could not before,
  and the reason was structural: the tree walk stopped at an argument, so
  everything past the address fell through to the configuration tree, which has
  no `remote-as` in it. Fixing it fixed the same gap inside `interface`, where
  `ipv4 address` completed nothing either.

* **Values that come from the box**: neighbours by address with the AS on the
  other end beside them, peer groups by name and labelled as groups, route-maps
  and prefix-lists read back from FRR, interfaces for `update-source`.

* **Four refusals, all structural.** Whether the neighbour answers is not in
  the configuration and is not claimed.

  ```
  error: neighbor 10.0.0.9 description sem-as: 10.0.0.9 has no 'remote-as':
  FRR refuses every other statement for a neighbour whose AS it has not been told
  ```

  A neighbour with no `remote-as`; a peer group nobody declared; a member whose
  group has no AS either (a member inherits it, so a neighbour with no AS of its
  own is correct when the group has one); and `update-source` naming an
  interface this box does not have — which FRR accepts, sourcing the session
  from nothing.

### Fixed

* **A BGP neighbour's password is no longer printed back.** It is the secret
  itself, not a hash, and it opens the session on the other router too. It was
  redacted nowhere: the account rule matched lines beginning with `password`,
  and `neighbor X password Y` does not. Now redacted in `show running-config`
  **and** in `show this` inside the block, which reads the routing lines
  straight out of the candidate.

### Internal

* `lab/routing.sh` no longer writes this side's BGP configuration. The session
  is configured **through the VTY**, so the three routes reaching the VPP FIB —
  including the BGP one — are evidence that the VTY configured a working
  peering. The password assertions were tightened after review: grepping for
  `not shown` alone passes on any box with a user password, and a `show this`
  that printed nothing would have satisfied the negative check.

* The per-lab cap in the suite runner is 600 s, not 300. `routing` measures
  router advertisements in three states and takes 329 s alone; killed at 300 it
  was reported as a failure, which is the cap saying something false about the
  lab.

## 0.50.0 — router advertisements off by default, and a refusal that cost the next commit

Three defects found chasing one lab failure
([#160](https://github.com/andrediashexa/hexarouting/issues/160),
[#161](https://github.com/andrediashexa/hexarouting/issues/161),
[#162](https://github.com/andrediashexa/hexarouting/issues/162)), following
[#133](https://github.com/andrediashexa/hexarouting/issues/133).

### Changed

* **This box no longer advertises itself as a router by default.** VPP does, on
  any interface holding an IPv6 address, every 200 seconds with a 600-second
  lifetime. On a subscriber-facing port that is a second default gateway for
  everyone who gets their prefix by DHCPv6-PD. Suppression is now applied when
  an IPv6 address is added, and `no ipv6 nd suppress-ra` is the exception an
  operator writes on the ports that should advertise.

  Measured on the wire, not read from a status line — `show ip6 interface`
  prints the same text either way. Counted in the peer namespace across each
  commit: **none by default, three once asked for, none again after
  suppressing**.

  Operators who rely on the previous behaviour MUST add `no ipv6 nd
  suppress-ra` under the interfaces that should keep advertising.

* **`commit` validates once.** It used to validate twice — once to discover
  re-mappings, once to decide whether to apply — and `apply()` prints what it
  refuses, so every validation error reached the operator twice. An invalid
  candidate is now reported as invalid before the re-mapping question, which is
  the more useful of the two answers.

### Fixed

* **A refused prefix-list range no longer costs the next commit.** A `ge`/`le`
  FRR rejects arrives through `mgmtd`, which keeps the rejected entry in its
  candidate datastore and then fails the **next** configuration commit with the
  same message — naming a prefix-list that is in no running configuration, on
  no disk, and in nothing the VTY sent. The VTY now refuses the line itself,
  so FRR never receives it:

  ```
  error: ip prefix-list PL-BAD seq 10 permit 10.12.0.0/8 ge 24 le 16: prefix
  range refused for 10.0.0.0/8: mask length <= ge <= le <= 32 does not hold
  (length 8, ge 24, le 16)
  ```

  The VTY now holds an opinion about what FRR accepts, and an opinion that
  drifts refuses configurations that are fine. So eight cases are put to both
  the VTY and a running FRR, and they MUST agree; `mgmtd` is restarted between
  cases so a poisoned candidate cannot make the next one look refused.

* **Router-advertisement state is read back from the dataplane.** It was the
  one interface attribute that was not, so the running configuration always
  said "suppressed" and `ipv6 nd suppress-ra` on an advertising port produced
  an empty difference, no call, and a commit that reported success having done
  nothing. Enabling advertisements could not be undone.

### Internal

* The router-advertisement lab measures all three states, and refuses to report
  a count from a capture that never opened. It had been capturing `veth-out`
  from the root namespace, where that interface does not exist: tcpdump exits,
  the file stays empty, every count reads zero, and "the default sends none"
  passed because nothing could be counted on either side of the setting.

## 0.49.0 — a pool's public prefix that nothing announces

The check this product can make and nobody else can
([#145](https://github.com/andrediashexa/hexarouting/issues/145), in part).

### Added

* **`commit` warns when nothing advertises a pool's public prefix.**

  ```
  warning: pool 'clientes': nothing here announces 200.150.0.16/28
      subscribers behind it would be translated to an address the internet
      cannot route back to
  ```

  Configure the CGNAT right, configure BGP right, and the service can still be
  dead: traffic leaves and nothing returns. The two halves are configured in
  different places by different people on different days, and until now nothing
  in the box connected them.

  Three properties, and the last two are what keep it useful:

  * **containment, not equality** — a `network 200.150.0.0/24` covers a pool's
    `/28`, and a shorter prefix can never be covered by a longer one;
  * **it reports and never refuses** — announcing from another box is a
    legitimate design, and a check that refuses a working topology gets worked
    around;
  * **it stays quiet when it cannot see** — `redistribute` can advertise a
    prefix in ways this check cannot follow. A warning that fires on a working
    box is worse than no warning, because the next one is ignored too.

### Worth recording

The lab asserts all three, and the order matters. "The warning stays quiet" is
**trivially true on a box where the warning never fires** — which is what
happened: the two silence assertions were written first, they passed, and the
feature was entirely dead. Only the assertion that proves it fires showed the
check was never reached.

That is the same shape as the ten vacuous assertions catalogued in
[#151](https://github.com/andrediashexa/hexarouting/issues/151) earlier the
same day, reproduced while writing a new test.

## 0.48.0 — prefix-lists complete from the box, and the check stops overclaiming

[#143](https://github.com/andrediashexa/hexarouting/issues/143) and
[#158](https://github.com/andrediashexa/hexarouting/issues/158), the second
found while measuring the first.

### Added

* **`ip prefix-list` and `ipv6 prefix-list` are in the command tree**, and a
  list an operator has written completes by name afterwards. The statements
  stay passed through to FRR deliberately: FRR stores them, the running
  configuration is read back from FRR, and `rollback` already restores them, so
  a model here would be a second copy of something that works. What was missing
  is the operator finding out what to type without leaving for `vtysh`.

### Fixed

* **`commit check` reported success for configurations the commit then
  refused.** It runs `frr-reload.py --test`, which computes the delta and
  prints what it *would* apply without handing the statements to FRR — so it
  exits 0 on a statement FRR itself rejects.

  `check` must not apply-and-roll-back to find out, so it reports its limit
  instead: CGNAT and the box's own configuration are checked, routing
  statements are checked for structure only and FRR can still refuse one at
  commit. That is true and actionable; the previous sentence was neither.

  The commit path itself is sound, and measuring that is why the fix is a
  message rather than a mechanism: a candidate carrying one invalid policy line
  applies **nothing** and rolls back, including the valid statements beside it.

### A correction

The planning issue for prefix-lists asserted FRR does not validate `ge`/`le`.
**It does** — `mask length <= ge <= le` and the family bound are both enforced,
with a clearer message than this VTY would produce. Half the checks planned
there would have duplicated a refusal the dataplane already makes.

### Worth recording

Three test defects were fixed in this suite's own guards, including one written
during this work that counted a marker's literal value while the tree refers to
the constant by name — and one that grepped for phrases the VTY does not print,
which passed for a whole run while the check approved a broken configuration.

## 0.47.0 — a policy whose daemon is stopped is refused, not lost

One defect ([#155](https://github.com/andrediashexa/hexarouting/issues/155)),
found by measuring FRR's real grammar rather than trusting what a planning
issue asserted about it.

### Fixed

* **`ip prefix-list`, `route-map`, `bgp as-path access-list` and the community
  lists have no `router` line**, so the daemon check keyed on one never looked
  at them. With the owning daemon stopped, FRR **accepts the statement and
  stores nothing** — no error, and the statement is absent from the running
  configuration afterwards. Measured on FRR 10.7.1.

  A `router bgp` stanza that goes nowhere is noticed: no session comes up. A
  prefix-list that goes nowhere is invisible until traffic that should have
  been filtered is not, and by then nobody connects it to a commit that
  reported success days earlier.

  The refusal names the daemon **and the file to edit**, because the fix is
  `/etc/frr/daemons` and a message that does not say so sends somebody to read
  their own configuration for an error that is not in it.

  Same shape as [#99](https://github.com/andrediashexa/hexarouting/issues/99),
  where `bgpd`, `ospfd` and `isisd` shipped disabled and routing configuration
  for daemons that were not running committed cleanly. That fix reported the
  daemons on an installed box; this is the half that was left.

### A correction to the plan

[#143](https://github.com/andrediashexa/hexarouting/issues/143) asserted that
FRR does not check `ge`/`le` on a prefix-list. **It does** — `mask length <=
ge <= le` and the family bound are both enforced, with a clearer message than
the VTY would produce. Half the checks planned there would have duplicated a
refusal the dataplane already makes. The issue is corrected against a running
FRR 10.7.1.

## 0.46.0 — the global settings leave the CLI socket, and fourteen tests that could not fail

Three merges in one release. The rule is one release per merge; these three
tell one story and the medium takes twenty-five minutes to gate, so they are
grouped and the deviation is recorded here rather than left implicit.

### Changed

* **Timeouts, block hold-down and session table sizing leave the VPP CLI
  socket** for `bpa_global_set`
  ([#76](https://github.com/andrediashexa/hexarouting/issues/76), still open
  for the `check` path). Every account that could change a timeout needed the
  socket, and an account with that socket can delete a pool and disconnect
  everybody behind it.

  A zero timeout field means "leave it alone" — zero is not a valid timeout,
  so it needs no separate flag. The settings that can legitimately be zero
  carry one.

### Fixed

* **`address-family` is a block**
  ([#150](https://github.com/andrediashexa/hexarouting/issues/150)). It was
  not, so `exit` inside one popped the enclosing `router bgp` and the
  operator's next line went to the top of configuration mode with the prompt
  still claiming BGP. The statement always reached FRR, so nothing failed —
  what was wrong is what the operator was told.

* **A refusal that named the wrong reason.** The session-table refusal
  returned a code and logged nothing, and the client attaches the last line of
  the dataplane's log so the operator gets a sentence rather than a number —
  so it attached an unrelated error and sent the operator to debug a file
  permission.

* **Fourteen assertions that could not fail**
  ([#151](https://github.com/andrediashexa/hexarouting/issues/151)), out of
  forty found weaker than they read. Three shapes:

  * **matching a label instead of a value** — `"pptp"` passed with the ALG
    off, including in the run that lab exists to fail; `"packets translated"`
    is declared once per direction, so the return path could translate
    nothing;
  * **matching a bare number or word** — `grep -qi up` matched a peer whose
    hostname is `upstream`; `grep -qc` is `grep -q`, so "all three routes"
    asserted "at least one";
  * **treating an empty result as a pass** — the check that the published ISO
    carries no usable root password accepted an unreadable `/etc/shadow`,
    passing without reading a byte of the image on any runner without
    `unsquashfs`.

  One of the fourteen was introduced the same day while deliberately replacing
  a hardcoded list with a rule. Making an assertion look stronger is not the
  same as making it stronger.

### Worth recording

Correcting the `grep -qc` showed `show route` listing one learned prefix
instead of two. **`show route` was right** — the prefix had left the FIB
because the lab's own MTU section drops the OSPF adjacency, and the restore
that should have put it back sent its output to `/dev/null`.

Two rules came out of chasing it: **a neighbour returning is not a route
returning** — the adjacency came back in 10 seconds and the route in 15 — and
**every section after a rollback test must re-establish its own
preconditions**, because a rollback restores an older revision and a `vppctl`
change pushes no revision for it to know about.

## 0.45.0 — the BGP plan, and the wiki rule made to stick

Planning and documentation. No behaviour change.

### Added

* **Phase 5 in the roadmap: BGP, route policy and prefix-lists**
  ([#141](https://github.com/andrediashexa/hexarouting/issues/141)), split into
  eight issues. BGP is passthrough today — `router bgp` opens a block the VTY
  never reads, and prefix-lists and route-maps are not in the grammar at all.

  The plan models the path an ISP actually uses, passes the rest through, and
  **makes the boundary visible**: a statement passed through unread is not one
  that was validated.

  The reason to do any of it is
  [#145](https://github.com/andrediashexa/hexarouting/issues/145) — three
  outages the box could refuse, one of them specific to this product. A CGNAT
  pool whose public prefix nothing announces leaves every subscriber behind it
  translated to an address the internet cannot route back to, with CGNAT and
  BGP each individually correct. Nothing connects the two today.

* **The interface block is documented on the wiki.** It went from two
  statements to ten across v0.40.0-v0.44.0 and the wiki covered none of it.

### Changed

* **`CONTRIBUTING.md` now carries what section 0 of `PADRAO-DOCUMENTACAO.md`
  keeps off a wiki page**: build instructions, labs, source file names, the
  binary API, `vppctl`, lab machine names, and issue numbers and GitHub links.
  The wiki is public and is written for whoever operates the platform.

  Recorded with the mistake next to it: a warning published earlier the same
  day cited two issue numbers and linked GitHub, on the first page the rule was
  applied to.

## 0.44.0 — the interface is a first-class object

Last of [#128](https://github.com/andrediashexa/hexarouting/issues/128)
([#133](https://github.com/andrediashexa/hexarouting/issues/133)). The
interface block went from `mtu` and `urpf` to ten statements over five
releases.

### Added

* **`mac-address`** on an interface. It matters on a card swap: the upstream's
  ARP cache, DHCP reservations and any L2 filtering all key on it.
* **`ipv6 nd suppress-ra`**. VPP advertises this box as a router **by
  default** — measured on a running dataplane: an interface holding an IPv6
  address sends a router advertisement every 200 seconds with a 600-second
  lifetime. On a subscriber-facing port that is a second default gateway for
  every subscriber who gets their prefix by DHCPv6-PD.

  **The default is unchanged.** Changing how the product behaves by default is
  a decision for the maintainer, not something the commit that adds the knob
  should do quietly. It is written down now, so it is a known default rather
  than an accident — which is what it was.
* **`show interface <name>`** in detail: state, MTU, addresses, the `linux_cp`
  pair, VRF, MAC, description, uRPF, whether CGNAT is attached, and the
  counters. The counters are printed uninterpreted, because during a fault the
  operator will also look at `vppctl`.

### Fixed

* **An exact interface name now wins over the prefix filter.** `show interface
  pg0` on a box that also has `pg0.100` matched both, so the detailed view
  never fired for any interface with sub-interfaces — which is every interface
  an ISP terminates subscribers on.

### Worth recording

The counter assertion in the lab named one counter, `rx packets`. The box
under test had moved `rx bytes` and `punt` off zero and not that one, so a
**working** box failed the test. It asserts the property now — that the report
does not say "all zero".

That is the fourth assertion in this phase written against the shape of an
answer rather than what has to be true of it. The others: a hardcoded list of
interface statements, twice, and `grep -A2` on a configuration block that grew
a line.

## 0.43.0 — an interface can be put in a VRF

Fourth of [#128](https://github.com/andrediashexa/hexarouting/issues/128)
([#132](https://github.com/andrediashexa/hexarouting/issues/132)).

### Added

* **`vrf <table>` inside an interface block.** Numeric, the same way a pool's
  `vrf` is numeric — two ways to name the same table would be two things to
  keep in step, and they would disagree. Table 0 is the default and is not
  written, so no box grows a `vrf 0` line under every port.

  Pools have carried a VRF since v0.18.0 and there was no way to put an
  **interface** in one, so the feature was half-built: the pool named a table
  nothing could be bound to.

  An interface whose VRF changes has its addresses re-applied inside the move.
  VPP **refuses** to move an interface that still holds addresses (`retval
  -114`), so the sequence is remove, move, re-add, as one operation. Doing it
  as two independent deltas produces the refusal; doing the move first and the
  addresses second produces a port briefly in the new table with nothing on
  it.

### A correction

The issue that asked for this stated, as fact, that binding an interface to a
VRF **clears** its addresses. It does not — VPP refuses the operation. The
behaviour was measured while implementing it and the claim in the issue was
wrong. The required sequence happens to be the same either way, but "clears
silently" and "refuses" lead to different code, so the record is corrected
rather than left standing.

## 0.42.0 — sub-interfaces, and a box with no FRR

Third of [#128](https://github.com/andrediashexa/hexarouting/issues/128)
([#131](https://github.com/andrediashexa/hexarouting/issues/131)), plus a
defect its lab uncovered
([#137](https://github.com/andrediashexa/hexarouting/issues/137)).

### Added

* **Sub-interfaces with VLAN tags.** `interface <parent>.<n>` with `vlan <id>`
  or `vlan <outer> inner <inner>`, and `no interface <name>` to remove one —
  taking what was on it, since an address left behind describes a port that
  does not exist. The whole interface block works inside a sub-interface.

  The suffix must equal the outer tag: VPP names a sub-interface after its
  `sub_id`, and the running configuration is read from the box, so the two
  names have to be the same name.

  **`exact-match` cannot be set.** `create_subif` takes a bitfield and `vat2`
  can send only one flag name of one, while an integer segfaults it
  ([#136](https://github.com/andrediashexa/hexarouting/issues/136)). QinQ
  sub-interfaces therefore also match frames carrying more than two tags.

### Fixed

* **A box with no FRR could not configure an interface at all.** Entering an
  interface block stages `interface <name>` as an FRR stanza, so every commit
  touching an interface triggered routing validation — and where FRR is not
  running that failed and rolled back the whole commit, CGNAT half included.

  `Frr.available()` existed, said *"A box may run CGNAT with no routing"* in
  its own docstring, and was never called. No lab caught it because every lab
  that drives `hexash` also starts FRR: the product's own supported
  configuration was not one any test ran.

### Worth recording

`lab/subif.sh` counts packets rather than configuration, injecting at
`ethernet-input` so the L2 tag actually has to steer the frame. Its second
assertion is the one it exists for: a frame with a different tag must **not**
land on the sub-interface.

Two assertions about the interface block hardcoded the list of statements and
had to be edited three times in a row as the block legitimately grew. They now
assert the boundary instead — everything offered belongs to the block, nothing
from the level above leaks in.

## 0.41.0 — addresses belong to the configuration

Second of [#128](https://github.com/andrediashexa/hexarouting/issues/128)
([#130](https://github.com/andrediashexa/hexarouting/issues/130)).

### Added

* **`ipv4 address` and `ipv6 address`** inside an interface block,
  repeatable, with `no ipv4 address <prefix>` removing one. Over the binary
  API, in the running configuration, restored by `rollback`. Until now they
  were set with `vppctl` and were therefore outside the candidate, the commit,
  the rollback and the running configuration.

  The keyword and the prefix must agree: `ipv6 address 10.0.0.1/24` is refused
  as a typo. `sw_interface_add_del_address` carries one prefix field for both
  families and infers the family from the value, so without the check the
  dataplane would take it and quietly configure something nobody wrote.

  Removals are applied before additions, and the delta compares sets so that
  adding one address does not re-apply the others — re-applying removes and
  re-adds, which on the interface carrying the session is a disconnection
  nobody asked for.

### Worth recording

Four test defects surfaced while writing this, all in tests rather than in the
product: an assertion that measured the aftermath of the section above it;
`grep -q 1500` on `show interface` matching a packet counter while the MTU sat
at 1450; a lab MTU restore that sent its output to `/dev/null` and had never
worked; and, once fixed, that restore breaking the rollback test three
sections away — a commit pushes a rollback revision, so inserting one shifts
what `rollback 1` means.

`lab/urpf.sh` also captured the interface block with `grep -A2`, so the new
address line pushed `urpf loose` out of the window. It reads to `exit` now.

## 0.40.0 — an interface can be described and shut down

First of [#128](https://github.com/andrediashexa/hexarouting/issues/128), the
phase that makes an interface a first-class object
([#129](https://github.com/andrediashexa/hexarouting/issues/129)).

### Added

* **`description <text>`** inside an interface block. It lives in the
  dataplane, as a VPP interface tag, and is mirrored to the Linux side as the
  interface alias — so it is read back from the box like the MTU, and `ip -d
  link` and FRR agree with the VTY instead of contradicting it.
* **`shutdown` / `no shutdown`**, over the binary API, reaching **both sides**
  of the `linux_cp` pair. Shutting the VPP interface alone leaves FRR holding
  adjacencies over a port that has stopped forwarding. Applied down first and
  up last, so an interface being taken down stops carrying traffic before its
  configuration changes underneath it.

### Fixed

* **The VTY was sending unrecognised interface statements to FRR.** `mtu` and
  `urpf` were intercepted by name and everything else fell through a catch-all
  into the routing section — and FRR accepts both `description` and `shutdown`
  inside an interface stanza, so nothing errored and neither reached the
  dataplane.
* **`local0` is no longer reported as administratively down.** It always is,
  so the running configuration said `shutdown` on every box before anybody
  typed it.

### Worth recording

Five assertions in the first version of this work could not fail: three read
`show running-config`, which includes FRR's half, so they matched the copy
that had gone to the wrong place; two matched `local0`. They now read the
dataplane — the tag from `sw_interface_dump`, the State column from `show
interface` — and one checks the description was **not** handed to FRR.

`flags: 0` segfaults `vat2`: administrative down is the absence of
`IF_STATUS_API_FLAG_ADMIN_UP`, and the client cannot render an empty flag set.
Down is sent as `IF_STATUS_API_FLAG_LINK_UP`, which VPP ignores except for the
absent `ADMIN_UP`.

## 0.39.0 — roles reach the dataplane

Progress on [#76](https://github.com/andrediashexa/hexarouting/issues/76),
which stays open: the global settings and the `check` path still go over the
CLI socket.

### Fixed

* **Roles were decorative on a real appliance.** `api-segment { gid vpp }`
  group-owns `/dev/shm/vpe-api` and not `/dev/shm/global_vm`; a client maps
  both, so a non-root account got `svm_region_map: Permission denied` and the
  VTY reported a timeout it could not explain. Every operator would have had
  to be root — the thing roles exist to stop.

  `lab/vty.sh` had been correcting this by hand since roles were built, which
  is the same shape as #119: a lab arranging an environment the product never
  produced. It is now a drop-in on `vpp.service`, asserted on the installed
  box, which is the only place the unit file is exercised at all.

  The boot self-test proves the property rather than the mode bits — not root,
  in the `vpp` group, can call — using `setpriv` so that nothing has to be
  created to test it.

### Changed

* **Three operations left the VPP CLI socket** for the binary API: attaching
  and detaching CGNAT on an interface (`bpa_interface_add_del_feature`, a
  message that already existed and was not being used), MTU
  (`sw_interface_set_mtu`) and uRPF (`urpf_update`). Both new message shapes
  were captured from a running dataplane rather than assumed, and both keep
  their read-back: `retval 0` says the message was accepted, not that the
  driver took the value.

  Twenty-eight CLI uses became twenty-three. Every remaining read is `show`
  passthrough, which is meant to stay.

### Known gap found here

* [#124](https://github.com/andrediashexa/hexarouting/issues/124) — attaching
  CGNAT to an interface is not recorded in the audit trail. It was a `vppctl`
  call before, so it was never attributable; moving it to the API made the gap
  visible rather than creating it.

## 0.38.1 — the gate refused 0.38.0, correctly

**v0.38.0 was tagged and never published.** `make release-gate` built the
medium, booted it, and the box reported itself NOT ready. That is the outcome
the rule exists to produce, so the tag stays and the release does not.

### Fixed

* **The NIC check added in 0.38.0 failed every live medium.** Without
  hugepages the tuner disables the DPDK plugin outright — the first-boot
  profile, because the EAL fails without them and `vpp.service` would
  restart-loop. So `startup.conf` names no device *and* carries no `no-pci`,
  and the new check read that as "may claim any NIC it finds".

  No DPDK means no device can be claimed by anything. That is the safe case
  and had to be told apart from "DPDK is on and nothing is named", which is
  the dangerous one.

## 0.38.0 — the dataplane took whatever NIC it found

One defect ([#123](https://github.com/andrediashexa/hexarouting/issues/123)),
found on a box installed from the v0.37.0 medium that came up unreachable.

### Fixed

* **The appliance handed VPP the management interface.** It shipped
  `HEXA_DATAPLANE_IF=eth0`, which does not exist on a Debian 12 box with
  predictable interface names. The tuner could not resolve a PCI address and
  generated `dpdk { dev default { ... } }` — no allowlist, no `no-pci` — so
  VPP's DPDK plugin probed and claimed what it could bind. On a single-NIC box
  that is the management interface, and the box left the network the moment
  the dataplane started.

  The installer never asks which interface carries subscriber traffic and
  nothing else writes that setting, so this was the ordinary case rather than
  the exception.

  **Unknown now means claim nothing.** The two outcomes are not symmetric: a
  box with no dataplane interface is recoverable by logging in over the
  network; a box that ate its own management is recoverable by walking to it.

  The interface carrying the default route is refused even when named, and the
  refusal is printed. The setting ships empty. The boot self-test says the box
  has no dataplane interface and names the file to edit, because a box that
  forwards nothing looks like a box that is working.

  **Recovering a box already in this state:** set `HEXA_DATAPLANE_IF` in
  `/etc/hexarouting/tune.env`, or add `no-pci` to the `dpdk {}` block in
  `/etc/vpp/startup.conf`, then **reboot** — DPDK bound the NIC to a userspace
  driver and stopping VPP does not reliably hand it back to the kernel.

  A test was holding the defect in place: `test_tune.py` asserted `"dev
  default" in out`, which reads as a harmless fallback and was the behaviour
  that removed boxes from the network.

## 0.37.0 — the medium never carried the binary API

Two defects in the installation medium, one hiding behind the other
([#119](https://github.com/andrediashexa/hexarouting/issues/119),
[#121](https://github.com/andrediashexa/hexarouting/issues/121)).

### Fixed

* **The ISO had never carried the vat2 plugin.** `build-iso.sh` copied the VPP
  plugin into the image and not the vat2 one; `install.sh` found neither of
  the two paths it looks in, printed a warning and carried on — a line that
  was in the build log of every release since the beginning.

  `hexash` creates and changes pools over that API. The appliance booted,
  answered, routed, ran its self-test and reported ready, and **could not be
  configured to do CGNAT at all**. Every lab that drives the API places the
  plugin by hand, describing that as "what install.sh does on a real box",
  which is why nothing noticed.

  The build now refuses without it and asserts afterwards on the file
  `install.sh` was supposed to place. The boot self-test reports whether the
  API is registered — read-only, because it runs at every boot on a production
  box.

* **`hexarouting.service` failed on the first boot of every install.** The
  installer writes the default configuration as three comment lines, and
  `hexash --load` read that as a complete configuration saying "no SSH
  access", on a box that has SSH access from the root password the installer
  just took. The lockout check refused it, correctly. An empty file is not a
  configuration that removes everything; it is a configuration nobody has
  written yet.

  `Config` counts the statements it parsed and `load()` returns when there
  were none — counted, not inferred from the fields, so a file setting a value
  that happens to be the default is still checked like any other.

  **This was introduced in v0.34.0** and is present in v0.34.0, v0.35.0 and
  v0.36.0. Before that change `load()` applied without checking first, so the
  empty file was a no-op.

### Changed

* **The release gate installs the medium to a disk and boots the disk**, with
  the medium detached. A live image booting proves the medium is bootable and
  does not prove it produces a working appliance; both defects above lived in
  the gap between those two claims. `CONTRIBUTING.md` rule 5 is now "built,
  booted, INSTALLED and booted again".

## 0.36.0 — applied, and never reported back

One omission ([#116](https://github.com/andrediashexa/hexarouting/issues/116))
in the configuration the box reports about itself.

### Fixed

* **`cgnat logging tcp <collector>` was absent from the running
  configuration.** It reached the dataplane and never reached `show bpa
  config`, therefore not `show running-config` either.

  The running configuration is read back from the box, not remembered, so this
  was not cosmetic. An operator auditing a box could not see where the
  compliance log was being sent; a rollback could not restore a destination
  that was never in the revision; and saving the configuration and reloading
  it produced a box that looked identical and **exported nothing over TCP**,
  with nothing reporting the difference.

  The lab now does the round trip: save the configuration, turn the exporter
  off, prove it is off, reload the saved configuration and see the collector
  return. A configuration that cannot be reloaded into the same box is not a
  configuration.

### Known issue found here

* Driving that reload surfaced
  [#119](https://github.com/andrediashexa/hexarouting/issues/119): **the ISO
  has never carried the vat2 plugin**, so on an installed appliance the binary
  API is unreachable and a CGNAT pool cannot be configured at all. Every lab
  that touches the API places the plugin by hand first, which is why nothing
  had noticed. Fix in progress.

## 0.35.0 — the compliance log was timestamped from VPP's boot

One defect ([#117](https://github.com/andrediashexa/hexarouting/issues/117)),
in the transport that exists because losing a record is not acceptable.

### Fixed

* **TCP log records carried seconds since VPP started, not the time of day.**
  The collector decoded allocations as happening at `00:00:05`. The UDP
  exporter, carrying the same events, decoded `14:04:43`.

  Records from different boots were **not orderable** — the clock returns to
  zero on restart, so the log ran backwards. The two transports could not
  corroborate each other, which is the only reason to run both. And a request
  naming a time of day, which is every request this log exists to answer,
  could not be resolved at all.

  `bpa_log_emit()` is now the single fan-out point and reads the wall clock
  itself rather than taking it as a parameter: the bug was a caller passing
  the clock it had at hand. `bpa_session.c` reached both exporters directly
  and now goes through that one function. `bpa_log_tcp_service()` keeps the
  monotonic clock, which is right for scheduling a reconnection and wrong for
  a record.

  The lab counted records, spill and replay across a collector outage and
  never looked at the time inside the record — which the collector had been
  printing as `00:00:05` on every run. Both IPFIX labs now assert the record's
  timestamp against the box's clock, an assertion whose error equals the
  machine's uptime and so cannot pass by accident.

  **Upgrade note:** a log already collected over TCP can be repaired if the
  box's boot time is known. Nothing recovers the ordering across a restart.

## 0.34.0 — a name the box does not have, and a field VPP does not print

Two parses written against a remembered format instead of a captured one
([#105](https://github.com/andrediashexa/hexarouting/issues/105),
[#114](https://github.com/andrediashexa/hexarouting/issues/114)).

### Fixed

* **An interface name the box does not have is refused at commit.** `interface
  Ten0` — one typo away from `TenGigabitEthernet0/8/0` — committed clean,
  appeared in the running configuration, and did nothing. The refusal lists
  what the box does have, because it already knows: it is the same source the
  completion answers from, and an operator handed only a "no" has to go
  looking.

  It covers all four places a name is staged: the FRR block, `cgnat
  interface`, and the `mtu` and `urpf` statements the VTY models outside the
  routing section. The first version of the check covered two of them, and
  every file that set an MTU passed unchecked.

  `hexash --load` refuses the **whole file** rather than statement by
  statement. It runs at boot with nobody watching, and applying half a file
  leaves the box holding half a configuration. With the dataplane unreachable
  the check is skipped, not failed — a VTY that cannot commit while the
  dataplane is down is a VTY that cannot be used to fix it.

* **`set_host_mtu()` had never run.** It looked for a `host` field in `show
  lcp`; VPP prints `itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap`
  and there is no such field, so the parse found nothing and returned —
  which is also what it correctly does on a box with no TAP, so the failure
  was invisible. `lcp-sync` is on by default and VPP was mirroring the MTU
  onto the TAP by itself, which is why the lab's assertion passed. **The
  assertion was measuring VPP, not this code.**

  Settled by turning `lcp-sync` off and committing again, at which point the
  TAP did not move. The lab now does exactly that before asserting, so it can
  only pass if `hexash` did the work. Both parses read every name on the pair
  line rather than the one in a fixed column; field order is a VPP
  implementation detail that has already differed between two labs here.

  Boxes running with `lcp-sync` at its default were correct by accident. A box
  with it turned off had a dataplane MTU and a Linux MTU that disagreed,
  silently.

### Changed

* **`tools/wiki-pull.sh` reads over the API instead of ssh-ing in as root** to
  query Postgres. Refreshing a read-only mirror required the machine's root
  password: the widest possible credential for the narrowest possible job. A
  token scoped to one account does the same work, is revocable from the web
  interface without touching the host, and cannot be reused to log in
  anywhere.

## 0.33.0 — only what is valid here, in any case, from the right side

Three defects in how the operator finds out what they can type
([#112](https://github.com/andrediashexa/hexarouting/issues/112)).

### Fixed

* **`?` inside a block offered the whole configuration tree** — fifteen options
  inside an interface, of which three were valid; thirteen inside `router bgp`,
  none of which were BGP. Offering the level above invites somebody to type
  `cgnat pool` inside an interface and find out afterwards what that meant.

  An FRR block now offers `exit` and `show` and nothing else: this VTY does not
  model FRR's statements, and offering some would be a guess presented as a
  grammar.

* **Completion was case-sensitive.** `ten`, `TEN` and `TenGig` now all complete
  to `TenGigabitEthernet0/8/0`, and it always inserts the box's own spelling.

* **`show interface` answered from FRR**, which sees only what `linux_cp` has
  mirrored — so on a box with a `GigabitEthernet0/12/0` configured, `show
  interface description` answered `lo`. It now reports the dataplane's
  interfaces with state, MTU, addresses and the `linux_cp` pair, and says so
  when the dataplane cannot be asked rather than printing an empty table.

### Added

* `tools/wiki-push.sh`, which **refuses to publish over a wiki page that
  changed after the mirror was pulled**. Four pages had been updated, the
  mirror was never refreshed, and a later edit was built from the stale copy —
  while the wiki was being reorganised. Nothing was lost, and only because the
  stale text failed to match.


## 0.32.0 — the prompt says where you are

Reported from a real session:

```
hexarouting# [edit] interface GigabitEthernet0/12/0
hexarouting# [edit] show this
interface GigabitEthernet0/12/0
hexarouting# [edit]
```

### Fixed

* **The prompt names the hierarchy and its subject**
  ([#108](https://github.com/andrediashexa/hexarouting/issues/108)):
  `[edit cgnat pool residential]`, `[edit interface TenGigabitEthernet0/8/0]`.
  Entering an interface moved nothing at all, so an `mtu 1500` typed one block
  later than intended went to the wrong interface with no warning.

* **`show this` inside an interface shows its MTU and uRPF.** It printed the
  header and stopped, because those are staged in `Config.mtus` and
  `Config.urpf` while that branch only looked at `Config.routing`. An empty
  answer is worse than no command: somebody who runs it and sees nothing
  concludes the block is empty.

* **Leaving a user or an SNMP user no longer prints an error.** A regression in
  v0.29.0: the `show` guard landed inside the `exit` branch, leaving dead code
  after a `return`. The whole suite passed — nothing left a block and checked
  that *nothing* was printed.

* **The parallel lab suite reports as it goes.** It judged every lab after the
  last one finished, so it printed nothing for twelve minutes — the same
  complaint [#97](https://github.com/andrediashexa/hexarouting/issues/97) fixed
  on the boot console.

### Added

* **The wiki is part of the work** — a rule in `CONTRIBUTING.md` §1 and
  invariant 1.9 of `docs/context.md`. Every task ends by asking whether a wiki
  page needs writing or changing, and the answer goes on the issue **including
  when it is no**.

* `wiki/`, a read-only mirror of the live wiki, refreshed with
  `tools/wiki-pull.sh`. The repository stays authoritative about the product;
  the wiki is a usage layer on top of it. No address or credential is kept.

  Applying the rule immediately found the wiki teaching **`show ip route`** —
  FRR's RIB, the table where a route looks correct on a box that is dropping
  the traffic. Public since v0.26.0. Four pages updated.


## 0.31.0 — the boot wait says what it is waiting for

```
[  **] Job hexarouting.service/start running (22s / no limit)
```

`no limit` is systemd's phrasing for a unit with no timeout. On a box somebody
is watching for the first time, that reads as *"this is not going to finish"*,
with nothing to say what is being waited for.

### Changed

* **The wait explains itself on the console**
  ([#97](https://github.com/andrediashexa/hexarouting/issues/97)), where the
  person is, rather than in the journal they cannot reach yet:

  ```
  waiting for the dataplane to accept commands
  (VPP is up; its CLI socket and plugins take a moment longer)
  still waiting: 5s of at most 60s
  the dataplane answered after 5s
  ```

  Three outcomes, told apart: a wait short enough that nobody noticed says
  **nothing**; a wait that ends normally leaves **no warning behind**; and a
  timeout says what it cost — *the CGNAT configuration has NOT been applied*.

  `lab/boot-iso.sh` fails a boot where the wait announced itself and never
  concluded. On a console that is indistinguishable from having hung.

  The 60-second bound stays, justified in the file rather than reduced: VPP
  answers in 5–21 s on a lab VM with no hugepage pool and one worker, and a
  box with 32 GB of hugepages and sixteen workers takes longer.

### Fixed

* `wait-for-vpp` truncated its own output on every line — invisible on
  `/dev/console`, total anywhere else.


## 0.30.0 — the connection records get their own filesystem

In dynamic mode the allocation log **is** the connection record the Marco Civil
requires this box to keep for a year. It shared a filesystem with everything
the box needs to keep working — so a log that fills stopped the box writing its
own configuration, its trail and anything else, because it was busy recording
who connected to what.

### Changed

* **The installer makes three partitions**
  ([#83](https://github.com/andrediashexa/hexarouting/issues/83), in part):
  ESP, root, and `/var/log/hexarouting`. Given its own filesystem, a full log
  volume is a full log volume.

  The default size is arithmetic and the arithmetic is on the screen: ~80 MB a
  day for 200k subscribers in dynamic mode, so a year is about 29 GB.
  Deterministic mode logs no allocations at all and needs almost none — which
  is why it is a question and not a constant. The root is never left less than
  8 GB.

  Mounted `nofail`: a box that will not boot because the records volume is
  missing is a box nobody can log into to find out why. The boot self-test
  therefore reports where the records are — without that, a box whose volume
  failed to mount comes up quietly writing them onto the root filesystem.

* `hexarouting.log-gb=` on the kernel command line, and with `hexarouting.yes`
  and no value the installer takes the default **and says so**. An unattended
  install has no console to answer from, and a question nobody can answer is an
  install that hangs.

### Fixed

* The disk size is read from `/sys/block/<disk>/size`, which is world-readable,
  so the fit is checked in `HEXA_INSTALL_CHECK` mode too. A check that skipped
  it for want of privileges would be more permissive than the real run.

### Still open on #83

The disk mirror, and verifying the disk that was just written before the
installer reports success.


## 0.29.0 — the VTY offers what the box has

`interface <TAB>` answered `<word>`: true, and useless. It told the operator
what they already knew and sent them to `vppctl` or `ip link` in another
window — the two tools this VTY exists to keep them out of — at the first
argument.

### Added

* **Completion of live values**
  ([#98](https://github.com/andrediashexa/hexarouting/issues/98)) wherever the
  tree takes a name:

  ```
  hexarouting# [edit] interface ?
    host-veth-vpp             interface (up)
    pg0                       interface (up)
    tap4096                   interface (up)
  ```

  Interfaces come from the dataplane with their state; pools, accounts and
  SNMP users come from the **candidate**, so they complete what is being
  edited rather than only what is committed. `local0` is never offered — it is
  VPP's own loopback and configures nothing.

  A source that cannot answer offers nothing and leaves the hint in place: a
  VTY that becomes unusable when the dataplane is down is a VTY that cannot be
  used to fix it.

* **`show this`** — the configuration of the hierarchy being edited and nothing
  above it, in every level the VTY has. It shows the **candidate**: somebody in
  the middle of an edit asking "what is here" is asking about what they are
  building.

  Inside `cgnat pool residential` there was previously no way to see what that
  pool holds without leaving and printing the whole box.


## 0.28.0 — the suite in a quarter of the time, and the release as one command

### Changed

* **The lab suite runs several labs at a time**
  ([#100](https://github.com/andrediashexa/hexarouting/issues/100)):
  **~35 minutes to 12m45s** at `LAB_JOBS=5`, on 8 cores.

  Measured before this existed, the series run sat at **load 1.18 across 8
  cores with an ISO build running alongside it**. The suite is not
  compute-bound and never was: each lab starts VPP, sleeps for packets to
  cross, and waits on timeouts.

  **Two runs produced identical verdicts**, which is the condition rather than
  a bonus — a parallel suite that is not reproducible has replaced a slow
  answer with an unreliable one. Attribution is asserted with a deliberately
  failing lab while five are in flight.

  The plugin is built **once** before any lab starts: `/src/.build-vpp` is the
  only shared writable state, and 36 labs each ran `ninja` in it.

### Added

* **`make release-gate`** — clone at the tag, build the plugin, build the
  medium, boot it, and refuse anything that is not the tagged tree. The
  procedure as one command, after it was got wrong three times in one evening
  and every one was caught by a rule rather than by attention. It refuses a tag
  that does not exist, a tag whose tree has a disagreeing `VERSION`, any
  `-dirty` or `-unverified` name, and a medium that builds and does not boot.

### Fixed

* In the suite runner: `rc` taken after the `printf` that writes the log is the
  **printf's** status. Every lab would have recorded 0, and one that exited
  non-zero without printing a failure marker would have been reported as
  passing — the exact defect this suite exists to catch, inside the suite.


## 0.27.0 — the daemons the VTY offers actually run

An installed box ran `zebra`, `mgmtd` and `staticd` — and **not one routing
protocol**. The VTY offers BGP, OSPF and IS-IS: `?` lists them, TAB completes
them, `commit` accepts them, and the daemon was not there.

### Fixed

* **`bgpd`, `ospfd` and `isisd` are enabled on an installed box**
  ([#99](https://github.com/andrediashexa/hexarouting/issues/99)). `frrinit.sh`
  starts `zebra`, `mgmtd` and `staticd` unconditionally; everything else is a
  line in `/etc/frr/daemons`, which Debian ships as `no` and the installer
  never touched.

  What hid it: `lab/routing.sh` proves all three work **by starting those
  daemons by hand**. The lab and the product disagreed about what runs, and the
  lab was the optimistic one.

* **`commit check` refuses a `router <protocol>` statement whose daemon is not
  running**, naming it. The silence was not otherwise guaranteed: `vtysh`
  prints `bgpd is not running` while `frr-reload.py` still returns 0, so the
  commit reported success for configuration that reached nothing.

### Added

* The boot self-test reports which routing daemons answer — as a warning, since
  a box deliberately running fewer is legitimate, but one that cannot run BGP
  should say so before somebody configures BGP on it.
* `lab/install-iso.sh` asserts them **on the installed disk**. That missing
  assertion is what let this ship: the lab tested a machine it had arranged
  rather than the machine that gets delivered.


## 0.26.0 — the table you can see is not always the table that forwards

`show ip route` in vtysh answers from FRR's RIB. The table that forwards is
VPP's FIB. An operator debugging *"the traffic is not flowing"* was being shown
the table where the route is present and correct, on a box that is dropping it.

### Added

* **`show route`** ([#95](https://github.com/andrediashexa/hexarouting/issues/95)),
  reading the **VPP FIB**:

  ```
  Destination          Next hop          Interface      Kind
  0.0.0.0/0            10.0.0.2          host-veth-vpp  forward
  203.0.113.0/24       10.0.0.2          host-veth-vpp  forward
  10.0.0.0/24          connected         host-veth-vpp  connected
  ```

* **`show route verbose`**, which reads both tables, marks every prefix where
  they disagree, and says what the disagreement means. The VPP column reports
  what VPP **holds** — `forward`, `connected`, `local`, `neighbour`, `drop`,
  `absent` — rather than yes/no: a prefix VPP is explicitly dropping and one it
  has never heard of are different situations with different causes.

* **`ip route` in the configuration tree**, discoverable, committed and rolled
  back like every other statement — and `commit check` refuses a next hop on no
  connected network, which would otherwise sit in FRR and never reach the FIB.

### What it deliberately does not flag

A **local address** — traffic to it is received, not dropped — and routes
through an **interface VPP does not own**, which on a real box is the
management port that must never be handed to VPP. The first version marked five
such prefixes on a healthy box. An alarm that is always on is an alarm that is
off.

### Found on the way

* [#99](https://github.com/andrediashexa/hexarouting/issues/99): an installed
  box ships with `bgpd`, `ospfd` and `isisd` **disabled** while the VTY offers
  all three — hidden because `lab/routing.sh` starts them by hand.
* [#98](https://github.com/andrediashexa/hexarouting/issues/98): the VTY
  completes keywords and stops at the first argument.
* [#100](https://github.com/andrediashexa/hexarouting/issues/100): the lab
  suite runs 35 labs in series on a machine at 15% load.


## 0.25.0 — the first five minutes with the box

Three defects reported from a real boot on real hardware, and a fourth found
underneath them.

### Fixed

* **`hexarouting.service` failed on the first boot of every install**
  ([#94](https://github.com/andrediashexa/hexarouting/issues/94)), for as long
  as the appliance has existed. `install.sh` writes `!\n! HexaRouting\n!` as
  the default configuration and the parser did not treat `!` as a comment, so
  `hexash --load` refused the file and exited non-zero.

  The banner this project writes was a syntax error to the parser this project
  reads.

* **The self-test reported `HexaRouting ready.` anyway.** It measured the
  dataplane, the routing, the tuning and the VTY, and never the unit whose job
  is to **apply** the configuration. It now checks that unit, and prints the
  last thing it said when it has failed — "failed" with no reason sends whoever
  is standing at the console to a journal they may not be able to reach yet.

  Each defect hid the other. Fixing either alone would have left the other
  standing.

* **The console looked hung until Enter was pressed**
  ([#93](https://github.com/andrediashexa/hexarouting/issues/93)). The
  self-test writes to `/dev/console` and the getty draws the VTY prompt on the
  same screen, with nothing ordering them. The getty is now ordered after the
  self-test **and** the installer, so an unattended install owns the console
  instead of running behind a login prompt.

* `boot_disk()` printed two lines when `PKNAME` was empty, so the medium the
  system booted from was offered in the install menu.

### Changed

* **The disk is chosen from a lettered menu**, largest first, with size and
  model. Enter takes the largest; a device path still works; the boot medium is
  never offered.

  ```
  Disks on this machine:
    a) /dev/sdb         932G  WD Black 1TB   <- default, the largest
    b) /dev/sda          32G  KINGSTON SA400
  ```

* **The confirmation no longer asks for the device path a second time.** It
  repeats the disk back with its size and model and takes the word `yes`. The
  property the old prompt protected is kept — a disk cannot be destroyed by
  holding down the return key — without a second transcription of the string a
  typo destroys.

* `tools/lab.sh` gives the ISO labs room for the virtual machine they boot, and
  installs the tools they need. It capped the container at 5 GB for an 8 GB VM,
  and did not install `sfdisk` although the Makefile's own target does — so one
  lab gave two answers depending on which command ran it.


## 0.24.0 — the image says which version it is

`hexarouting.iso` says nothing about what it is. A file keeps its name and
loses its context the moment it leaves the release page — on a USB stick, in a
downloads folder, handed to somebody at a site — and two of them are
indistinguishable at exactly the moment it matters, which is when an operator
is deciding which one to install.

### Changed

* **The installation medium is named `hexarouting-<version>.iso`**
  ([#91](https://github.com/andrediashexa/hexarouting/issues/91)), and
  `CONTRIBUTING.md` §5 requires it.

  The bare versioned name is **reserved for the tree that is the release** —
  clean, and standing exactly on the tag for the version it claims. Anything
  else says which tree it came from:

  | Tree | Name |
  |---|---|
  | clean, on `v0.24.0` | `hexarouting-0.24.0.iso` |
  | clean, past the tag | `hexarouting-0.24.0-49fc5de.iso` |
  | dirty | `hexarouting-0.24.0-49fc5de-dirty.iso` |

  That is the same argument one step further: two images of the same version
  built from different working trees would otherwise share a name, and the one
  nobody must mistake for a release is precisely the one built from
  uncommitted changes.

  `tools/version-stamp.sh --filename` is the only place the calculation lives,
  for the reason that file already gives: a second copy would drift, and the
  drift would surface as an ISO whose name disagrees with the version inside
  it. The ISO labs no longer hardcode the name — they ask for it.

### Fixed

* `.gitignore` matches `hexarouting*.iso`, both names. Narrowing it to the
  versioned pattern alone un-ignored the image already on disk under the old
  name, and `git add -A` committed 321 MB of it — caught only because GitHub
  refuses files over 100 MB. A rename that changes what a glob matches is a
  rename that changes what is ignored, and that is not obvious from either
  half of the change.


## 0.23.0 — the trail defends itself, and says where it stops

The trail was append-only and `fsync`ed, so it survived a reboot and a crash.
What it did not survive was **editing**. Nothing detected a line changed or
removed, which means it proved what happened only to somebody who already
trusted the operator — and it exists to be shown to somebody who does not.

v0.21.0 made that more urgent rather than less: a trail that names people and
can still be rewritten invites exactly the dispute it was meant to settle.

### Added

* **A hash chain over the configuration trail**
  ([#22](https://github.com/andrediashexa/hexarouting/issues/22)). Each entry
  carries a sequence number and a SHA-256 digest over the previous digest and
  its own text, so an alteration cannot be made to verify by fixing up the line
  it is on.

* **`show bpa pool history verify`**, which reports the **first** place the
  chain breaks and tells three different failures apart:

  | What was done | What is reported |
  |---|---|
  | an entry's text changed | `BROKEN at entry 2`, with the entry |
  | an entry removed from the middle | `BROKEN before entry 4`, the sequence jumps |
  | entries removed from the end | `TRUNCATED: ends at 5, we wrote 7` |

  The sequence is checked **before** the digest, and that order is the
  diagnosis: removing an entry also breaks the digest of the entry after it, so
  checking the hash first reports every removal as an alteration of an innocent
  line.

* **The chain head is recovered at startup**, so a restart continues one chain
  instead of beginning a second — otherwise every verification after a restart
  reports a break at the restart, and an alarm that is always on is off.

* A trail with no chained entries is reported as **not covered**, never as
  intact: a verifier that answers "clean" to an empty file has been defeated by
  `rm`.

### Fixed

* `bpa audit-path` built the path with `unformat %v` and never NUL-terminated
  it before handing it to `open()`, so the plugin opened whatever bytes
  followed in memory — failing or succeeding depending on the allocator.

### Changed — project rule

* **Every release now builds *and boots* the installation medium**
  (`CONTRIBUTING.md` §5). The product is the appliance, and every release
  before this was verified as source and verified as a bootable medium only
  when somebody thought to check. `make iso` produces a file; only
  `make test-iso` produces evidence. A release whose ISO does not boot **MUST
  NOT** be published.

### Known limit

The chain does not stop somebody with write access to the file **and** this
algorithm from rewriting the trail and recomputing every digest. Nothing kept
only on the box can. What makes it evidence against its own author is the copy
of the digests in a remote syslog — which the entries already reach. Stated in
[docs/logging.md](docs/logging.md).


## 0.22.0 — a page to read first

The design was recorded in sixteen places, each written for a reader who
already knew why that file existed. None of them was written **first**, so
every analysis began by reconstructing the shape: that the mapping is
arithmetic and therefore that changing a pool definition re-maps every
subscriber; that the trail is the proof because deterministic mode logs no
allocations; that a counter is not evidence.

### Added

* **[docs/context.md](docs/context.md)**
  ([#87](https://github.com/andrediashexa/hexarouting/issues/87)) — nine
  invariants that are not obvious from any single file, each naming the file
  that is **authoritative** for it; a map of what question each part answers
  rather than what it contains; and a reading order per task. Linked from
  `README.md` as the first stop.

  It deliberately does **not** restate the design. Two descriptions of one
  architecture diverge, and a reader cannot tell which one is stale.

  It also names what is deliberately absent — HA, throughput figures, per-flow
  logging, NPTv6, a graceful pool migration, the IANA enterprise number — so an
  analysis stops reporting decisions as gaps.

* **`test/test_context_map.py`**, in `make test`: every repository path the
  document names must exist, and `README.md` must still link to it. A map that
  names files which are no longer there is worse than no map — it is
  confidently wrong. It fails when it finds fewer than thirty paths, so a
  broken regex cannot make it pass by finding nothing.

  It does not check that the prose is still true. Nothing can, and the test
  says so rather than implying more than it does.


## 0.21.0 — the trail says who, not only what

The configuration trail carried the complete pool definition, dated in UTC,
append-only and `fsync`ed — and nothing identifying a person. It said *"the
pool changed at 14:32 on the 10th"* and could not say who changed it. That is
the difference between a log and an audit trail: a log records events, an audit
trail records accountable actions.

### Added

* **Operator identity on every trail entry**
  ([#34](https://github.com/andrediashexa/hexarouting/issues/34)):

  ```
  2026-09-07T22:59Z limits pool provider-b by marina uid 1001 via api inside ...
  2026-09-07T22:59Z limits pool provider-b by unattributed via cli inside ...
  ```

  Captured from the request at the moment the change is **applied**, and
  cleared immediately after, so an entry can only ever carry the identity of
  the request that produced it.

  The identity travels in each message because the transport leaves no
  alternative: `hexash` reaches the dataplane through vat2, one process per
  call, so there is no connection to attach it to — and shared memory carries
  no peer credentials the plugin could read instead. Five mutating messages
  gained `audit_user` and `audit_uid`, which **changes their layout**; a client
  built against 0.9.0 must be rebuilt.

* **An entry that cannot be attributed says so**, and a change typed at the VPP
  CLI is distinguished from an API client that sent nothing. Omitting the field
  would make either indistinguishable from an entry written before this
  existed.

* `SUDO_USER` is preferred over the effective account: an operator who runs the
  VTY under `sudo` is root by the time the call is made, and a trail recording
  `root` for all of them has recorded nothing. The uid goes on the line as well
  — `marina uid 0` says marina acting as root, which neither field says alone.

### Fixed

* Two paths that would have produced **false attribution**, which is worse than
  none: `bpa_main` starts zeroed, so every change made at the VPP CLI would
  have been recorded as uid 0 — root; and a field a client does not send
  arrives as zero on the wire, which is a real uid. Both now resolve to
  unattributed.

### Known limit

The client asserts its own identity and the plugin cannot check it. Anyone who
can reach the API can already reconfigure the box, so the trail records which
operator did what among people trusted to be there — it is not evidence against
them. Kernel-verified identity needs the socket transport
([#76](https://github.com/andrediashexa/hexarouting/issues/76)); tamper-evidence
is [#22](https://github.com/andrediashexa/hexarouting/issues/22). Both are
stated in `docs/logging.md`.


## 0.20.0 — the cheapest test level was red, and the silent code had no test

`make test` is level 1 of the pyramid in `TESTING.md`, the one that "runs
before every commit". It had been failing since v0.15.0 on any machine whose
account is not root — which is worse than the three failures it printed: a
suite nobody can run green is a suite whose next real failure is
indistinguishable from the noise it already prints.

### Fixed

* **`make test` passes on an ordinary developer account**
  ([#85](https://github.com/andrediashexa/hexarouting/issues/85)).
  `test_hexash_apply.py` builds a `Shell`, whose constructor reads the invoking
  account's real group membership (#35). The test now states the role it runs
  under — and asserts that a read-only account **is** refused, so granting
  capabilities in the fixture cannot be mistaken for deleting the check.

* Three junk files committed at the repository root, fragments of a shell
  heredoc that were interpreted as filenames.

### Added

* **`test/test_bpa_mss.c`** — the TCP MSS clamp, 47 checks. That it only ever
  **lowers**; a SYN with no option; NOOP and EOL padding; a zero and a
  too-small length byte; a length pointing outside the header; an MSS option
  cut short by the data offset, where the bytes rewritten would be payload; a
  data offset past the end of the packet and one below the minimum header.
  Every checksum is compared against a full independent recomputation, never
  against the incremental arithmetic that produced it.

* **`test/test_bpa_ftp.c`** — the FTP sequence shift, 63 checks. `bpa_seq_after`
  across the 2^32 boundary, which exists because plain `>` is wrong there and
  no lab is going to send four gigabytes to find out; a retransmission of data
  sent **before** a rewrite; a control connection rewritten **twice**; and
  `bpa_ftp_is_control` for the subscriber's own FTP server, where the port is
  read off the session because `in2out` has already replaced the one in the
  header.

* **`test/test_hexash_config.py`** — which changes rebuild a pool and which
  apply in place, as a table, plus an assertion that the table **covers every
  field a pool has**. A field added later is then a failure rather than a
  silent default, and the default decides whether an operator loses their
  subscribers.

* `test/bpa_tcp_shim.h`, the same standalone pattern `bpa_ports.h` and
  `bpa_rtsp.h` already use. `bpa_mss.h` and `bpa_ftp.h` gained the
  `BPA_STANDALONE` guard; no behaviour change in the VPP build.

### Method

Every new assertion was run against deliberately broken code and required to
fail before being kept — 18 mutations. Three were not caught on the first
attempt, and each was a real hole: an MSS case that did not exercise the bound
it was named after, an FTP acknowledgement path whose subtraction was
unobservable because every case ran with zero drift, and a mutation harness
that mutated a file nothing compiled.

## 0.19.0 — a change that re-maps subscribers has to be asked for by name

In deterministic mode a subscriber's public address and port block are a
function of the pool definition. Adding one public prefix changes the divisor,
which moves every subscriber at that instant and ends every session on the
pool. Nothing is corrupted and past answers stay resolvable, but every customer
is disconnected at once and the box reports nothing unusual — because nothing
unusual happened. It did what it was told.

### Added

* **`commit` refuses a change that re-maps existing subscribers**
  ([#23](https://github.com/andrediashexa/hexarouting/issues/23)), and says in
  numbers what it would do:

  ```
  error: this change re-maps subscribers that already have mappings
      240 of 256 subscribers change public address or port block; 0 hold live state right now
      100.64.64.16 moves from 203.0.113.0:1536-2047 to 203.0.113.0:2048-3071
      if that is what you mean, type: commit remap
  ```

  The count comes from the dataplane, which holds both definitions and runs the
  same arithmetic it would run afterwards — not from a second implementation of
  the mapping in the VTY. `commit check` reports it without applying anything.
  Sessions currently held are counted separately: the mapping moves for
  everybody, but the customers cut off are the ones with live state.

* **A rollback that re-maps is refused too.** Undoing a change that moved every
  subscriber moves them all a second time, so `rollback` is not a way round the
  acknowledgement.

* **[docs/pool-resize.md](docs/pool-resize.md)** — what moves and what does
  not, how to measure it before committing, the procedure that costs an outage,
  and the migration that costs none: add capacity as a second pool rather than
  growing the first. It also records what is deliberately not offered and why.

### Fixed

* Two labs were quietly measuring nothing once the refusal existed:
  `lab/commit.sh` section 4 changed a block size that was no longer being
  applied, leaving the rollback assertion after it passing for the wrong
  reason, and `lab/in-place.sh` section 3 — the control proving that a mapping
  change *does* drop sessions — had become a no-op, which would have left
  "in-place changes keep every session" passing on a box that never dropped a
  session for any reason. Both now ask for the re-mapping by name and assert
  the refusal on the way.

* An assertion in `lab/commit.sh` read the revision history at verdict time
  rather than at the moment it was about.

* Two labs were found to have quietly stopped applying their own changes once
  the gate existed, which is how a control fails: not by failing, but by
  passing for the wrong reason. Both were caught by running the suite.

### Internal

* [#83](https://github.com/andrediashexa/hexarouting/issues/83) added to the
  roadmap: the ISO installer does everything right up to the moment the disk is
  written, and nothing after it — no management address, no home for the log
  that is not the root filesystem, no mirror, and no check that the disk it
  just wrote will boot.


## 0.18.0 — two tenants, one private range

A provider does not choose what private addresses its customers' CPE hands out.
Two tenants on one box will use `100.64.0.0/22`, and before a pool could name a
VRF there was nothing to say which of them a packet belonged to: the first pool
that claimed the address answered for both, and the second translated nothing
while appearing in `show` and accepting configuration.

### Added

* **Pool selection by VRF**
  ([#79](https://github.com/andrediashexa/hexarouting/issues/79)):

  ```
  cgnat pool tenant-a
   inside 100.64.0.0/22
   outside 200.150.0.16/30
   vrf 1
   exit
  ```

  Absent, a pool serves every VRF — what every configuration written before
  this existed means, and a **different answer from VRF 0**. `outside-vrf`
  names the VRF translated traffic leaves by.

* **Overlapping selectors are refused**, naming the pool they collide with.
  A VRF is what makes an inside overlap legitimate, so the refusal applies only
  when both pools could answer for the same packet. Public addresses are
  compared regardless of VRF: the return path finds a pool by public address
  and has no VRF to separate two answers. That closes
  [#73](https://github.com/andrediashexa/hexarouting/issues/73).

### Changed

* **The session key carries the VRF, and split in two.** Its second word was
  reserved for a VRF id and
  [#44](https://github.com/andrediashexa/hexarouting/issues/44) had already
  spent it on the remote address that separates two PPTP servers choosing the
  same Call ID — so the two are **packed**, not chosen between.

  | | Key | VRF |
  |---|---|---|
  | `bpa_key_in` | private address, port, protocol, VRF | yes |
  | `bpa_key_out` | public address, port, protocol | no |

  The qualifier is the **packet's** VRF, because the session lookup happens
  before the pool is known. One vector element on a buffer the node already
  holds — a packet on an existing session pays nothing.

* **A session carries the VRF its subscriber's traffic arrived in**, and the
  reply is forwarded in it. Otherwise the reply is looked up in the outside
  interface's table and dropped for having no route, which reads as a
  translation failure rather than a lookup in the wrong table.

* **Binary API 0.9.0** adds `vrf_id` and `out_vrf_id` to `bpa_pool_add_del` —
  that message and not the settings one, because changing which VRF a pool
  serves changes which subscribers it maps, exactly like a change of prefix.

### Fixed

* **`lab/smoke.sh` was three pools of which only one translated.** It created
  `provedor-a` with `inside 100.64.0.0/12` and two more claiming
  `100.64.0.0/16` — inside the first. The other two existed, accepted
  configuration and did nothing. #73 was living in the project's own smoke test
  since the beginning, and the refusal added here is what found it.

## 0.17.0 — seeing a pool fill, before it has filled

RFC 8158 `natEvent 12` reports port exhaustion, which is to say it reports the
moment a customer **started losing traffic**. By the time it fires the support
call is already in the queue.

### Added

* **Utilisation as a distribution**
  ([#27](https://github.com/andrediashexa/hexarouting/issues/27)).
  `show bpa pool` reports the shape rather than a number:

  ```
  utilisation  2 subscriber(s) holding a block
     75-89%  1
        100%  1
  alarm at     75% of a subscriber's block
  ```

  With 200k subscribers a pool reads as half empty while a thousand customers
  sit at their limit, and an average says nothing about either. `show bpa top`
  remains the other half — the histogram says *how many*, `top` says *which*.

* **`utilisation-alarm <percent>`** per pool, off by default. One line when a
  subscriber's blocks reach the threshold, naming it, how full it is and what
  it is ahead of — and then **nothing more**, however much further it fills.

  That is the design and not an optimisation: a line per allocation past the
  threshold is a log nobody can keep and an alarm nobody reads, and the
  subscriber that trips it is by definition the one allocating fastest. Ten
  points of hysteresis clear the flag so a subscriber sitting on the threshold
  does not produce a line every time a port is freed and taken.

  Evaluated where a port is **taken** and nowhere else. Utilisation only rises
  there; it falls on the release path, and the flag is cleared on the next
  allocation rather than by touching that path. Nothing is counted per packet
  and the session table is never walked.

### Changed

* **Binary API 0.8.0** adds `util_alarm` to `bpa_pool_settings_set`, so turning
  the alarm on does not rebuild the pool and disconnect the subscribers it
  exists to watch.

## 0.16.0 — the number that trades one failure for another

Shortening a timeout is the first thing anybody reaches for when a pool fills,
and it works: ports recycle faster and more subscribers fit on one public
address. What it also does is cut connections that are **idle rather than
finished** — an SSH session left open over lunch, a database pool, a sensor
that speaks once an hour. Those come back weeks later as *"it disconnects at
random"*, which nobody attributes to a number changed in another month.

### Added

* **A warning when a timeout is set below the floor its RFC asks for**
  ([#54](https://github.com/andrediashexa/hexarouting/issues/54)):

  | Timeout | Floor | Reference |
  |---|---|---|
  | `udp` | 120 s | RFC 4787 REQ-5 |
  | `icmp` | 60 s | RFC 5508 REQ-2 |
  | `tcp-transitory` | 240 s | RFC 5382 REQ-5 |
  | `tcp-established` | 7440 s | RFC 5382 REQ-5 |

  The value is **applied**: these are SHOULDs, an operator may have a reason,
  and a box that refuses is less useful than the one they are replacing. The
  message says so in as many words. It names the RFC, the floor and what
  breaks, and reaches both the operator who set it and the log — because the
  person who shortens a timeout and the person diagnosing the disconnections
  six weeks later are not the same person. Once per change, never per packet.

  A value **at** the floor says nothing at all: a warning that always fires is
  one an operator learns to skip, and that is asserted rather than assumed.

* **`show bpa timeouts`**, which did not exist — the SNMP agent had already
  been caught assuming it did. Marks every value under its floor, globally and
  per pool with the overrides resolved, so the state is visible to somebody who
  did not know to ask.

The floors, their references and their consequences are **one list** in
`bpa_pool.h`. The command that sets a timeout, the warning it prints and
`show bpa timeouts` read the same list; through the VTY, `hexash` reads the
marks back from the device rather than recomputing them in Python, which would
be a second thing to keep in step.

## 0.15.0 — an account that can look without being able to change

There were no roles. Anyone who could log in and belonged to the `vpp` group
could create a pool, **delete a pool** — disconnecting every subscriber behind
it — or change a block size, which re-maps every subscriber at once. And there
was no read-only access, so an operator who only needed to look at sessions had
to be given the ability to change them.

### Added

* **Roles in the VTY** ([#35](https://github.com/andrediashexa/hexarouting/issues/35)),
  mapped onto **system groups** rather than a database this appliance keeps of
  its own — an appliance that invents its own accounts is one whose accounts
  nobody deprovisions:

  | Group | May change |
  |---|---|
  | `hexa-admin` | everything: routing, the box's own configuration, CGNAT |
  | `hexa-cgnat` | CGNAT configuration only |
  | neither | read-only |

  The check lives in `Shell.apply()`, which `commit`, `rollback` and `--load`
  all reach. The gates on `configure` and on the shell escape are convenience:
  a check that only hides a command from the completion tree is not an
  authorization check, and `lab/vty.sh` issues the commands directly for that
  reason.

  Authorization answers **before** validation — a CGNAT operator committing a
  routing change is told they may not, rather than that FRR is unhealthy, which
  would be the wrong answer and one they were not entitled to.

  `hexa-admin` is **additive**: it grants the capability, not file access.
  `/etc/hexarouting` is group-owned by `hexa-cgnat`, so an administrator must
  be a member of both — otherwise they can apply a change and then fail to
  record its rollback revision.

### Changed

* **The shell escape is restricted as well as hidden.** `docs/routing.md`
  already said hiding was not a boundary and pointed at this issue; this is
  what makes the sentence true. A shell on this box is every capability at
  once.
* **`api-segment { gid vpp }`** in the VPP configuration, and
  `/etc/hexarouting` group-owned and `0770` rather than root-only. Without
  both, no non-root account can use the binary API or record a rollback
  revision, and roles would be decorative.

### Fixed

* **A rollback store the account cannot write raised `PermissionError` out of
  the commit path.** The change is already on the dataplane by then, so the
  traceback left the operator with a Python stack and no idea whether their
  commit took effect — it had. It now says exactly that, and that `rollback`
  will not be able to undo it.
* `core` is in `.gitignore`. The labs run VPP with `full-coredump`, so a crash
  in a probe drops a 60 MB file in the working directory — and one of them got
  as far as a pushed branch before anybody noticed.

### The boundary this does not draw

Every role still needs the **VPP CLI socket**, which is total control of the
dataplane, so the roles bound what the VTY will do and not what a determined
account can reach. Written into `docs/routing.md` rather than implied, and
filed as [#76](https://github.com/andrediashexa/hexarouting/issues/76) —
finishing what [#3](https://github.com/andrediashexa/hexarouting/issues/3)
started.

## 0.14.0 — a setting an operator can change with somebody on the phone

Every per-pool setting had an in-place command on the dataplane, and none of
them worked through the VTY. `hexash` replaced any pool whose configuration
text differed, so a commit that changed one ALG switch, one timeout, one
filtering mode or the MSS clamp tore the pool down and built it again —
disconnecting every subscriber on it. That is worst for exactly the settings an
operator reaches for during an incident.

### Changed

* **A per-pool setting changes in place**
  ([#69](https://github.com/andrediashexa/hexarouting/issues/69)). A pool now
  has two kinds of setting and the distinction lives in one place:

  | | |
  |---|---|
  | **the mapping** — prefixes, block size, port range, policy, mode | every session on the pool is invalid the moment these change, so the pool is rebuilt and its sessions go with it |
  | **everything else** — filtering mode, ALG switches, `max-blocks`, per-pool timeouts, the MSS clamp, and the bounds, forwards and bypass lists that already had their own calls | none of these change what a port means, so none needs the pool rebuilt |

  `bpa_pool_settings_set()` takes exactly the second list, under **one** worker
  barrier, and the VTY sends it as **one** message — five separate messages
  would be five chances to leave a pool half-changed.

### Fixed

* **Deleting a pool did not free its sessions.** Left behind they outlive the
  definition that produced them, and since VPP reuses the pool index a pool
  created afterwards inherited sessions carrying the **old** arithmetic: a
  subscriber still translated onto a public port that the new mapping assigns
  to somebody else, with nothing anywhere saying so. Found while building the
  lab for #69, whose own acceptance criteria already claimed those sessions
  went with the pool.

### Added

* **`lab/in-place.sh`**, which measures both directions on a pool carrying
  traffic: five settings in one commit with every session kept and no `del
  pool` in the trail, then a block-size change with the sessions dropped and
  the pool rebuilt. The second half is what makes the first mean anything.

### Filed, not fixed

* [#73](https://github.com/andrediashexa/hexarouting/issues/73) — two pools may
  claim the same inside prefix, and the first silently wins.
* [#74](https://github.com/andrediashexa/hexarouting/issues/74) —
  `show bpa subscriber` can report *no live state* while the session table
  holds that subscriber's sessions. Not reproducible in isolation yet.

### Changed

* **Binary API 0.7.0** adds `bpa_pool_settings_set`.

## 0.13.0 — traffic that must not be translated

Some destinations have to see the real source address: the operator's own
management network, an internal server, a CDN cache that accounts by client
address, a government portal that refuses shared addresses. And some sources
must not be translated at all — a customer with their own public address on the
same access VLAN. Without a way to say so, the only way to exempt traffic was to
keep it out of the pool entirely, which is not always possible.

### Added

* **NAT bypass lists** ([#25](https://github.com/andrediashexa/hexarouting/issues/25)):

  ```
  cgnat pool residential
   bypass destination 10.0.0.0/8
   bypass source 100.64.1.0/24
   exit
  ```

  A bypassed packet takes **no port, creates no session and writes no log
  record**. `lab/bypass.sh` asserts both halves — the source read off the
  **captured** packet, and the session count before and after — because a
  bypass implemented after the allocation looks identical on the wire and
  passes the first half alone.

  The lists are `bpa_addr_set_t`, the structure the pool's own prefixes already
  use: inline, so a lookup is a binary search over a small array and never an
  allocation. The check runs after the session lookup has missed and the pool
  is known; a bypassed flow therefore pays that lookup on every packet, which
  is the stated price of allocating nothing for it.

  Lists are replaced entire rather than edited prefix by prefix — merging means
  the stored set no longer remembers what was typed — and applied in place, so
  exempting a destination does not disconnect the pool.

### Fixed

* `bpa_addr_set_build()` refuses an empty set, correctly for a pool's own
  prefixes and wrongly for a bypass list, where empty is the normal state.
  Building it unconditionally made **every** `bpa pool` command fail. Found by
  the new lab on its first run.

### Changed

* **Binary API 0.6.0** adds `bpa_bypass_set`.
* **`docs/roadmap.md` carries the plan for everything that remains**, written
  after reading all 26 open issues rather than their titles: seven phases
  ordered by what each item costs while it is absent, and the four dependencies
  that reading them turned up.

## 0.12.0 — bounding one customer without touching the others

One infected host opens thousands of sessions and exhausts **its own** block.
The neighbours sharing its public address are unaffected — that is the point of
a block per subscriber — and it is also why the operator has nothing to point
at when that customer calls, so the CGNAT gets blamed for an infection on the
far side of the access network.

### Added

* **Per-subscriber session and rate limits**
  ([#18](https://github.com/andrediashexa/hexarouting/issues/18)):

  ```
  cgnat pool residential
   max-sessions 512
   max-session-rate 50
   exit
  ```

  Zero is unlimited in both, so an upgrade refuses nothing that worked
  yesterday. `bpa limits` changes them on a running pool.

  Checked in `bpa_session_create()`, right after the subscriber record is
  resolved and **before a port is taken** — nothing is added to the fast path,
  because a packet matching an existing session never reaches the function and
  the record was already looked up in order to allocate from it.

  The rate is a **token bucket**, not a one-second window: a window lets a
  scanner spend two windows' worth of budget across the boundary, which is
  exactly the burst the bound exists to stop. The bucket holds one second's
  worth, so the allowed burst is a number the operator agreed to.

* **Three refusal counters, per subscriber**, in `show bpa subscriber` and on
  the node — block full, session cap, rate cap. They need three different
  answers: sell the customer more ports, raise the cap or accept it is doing
  its job, or go and look at the machine. One counter for all three would
  answer none of them.

* **`lab/limits.sh`**, whose central check is **isolation**: a subscriber past
  its cap is refused while a second subscriber on the *same public address*
  carries on. A cap that bounded the pool rather than the customer would pass
  every other check in that lab and fail exactly that one.
  `sh tools/lab.sh limits off` fails 11 checks.

### What the bounds deliberately do not do

* **They never refuse a mapping an ALG opens** for a control connection that
  already passed them — an FTP data connection, an announced media port, a PPTP
  call. An announced port that does not work is the failure an ALG exists to
  prevent, and a subscriber cannot reach thousands of them without first
  opening thousands of control connections, which the bounds *do* see. They
  still **count** toward the concurrent total, because they occupy a port like
  anything else.
* **They never close a session that already exists.** A cap bounds what may be
  created; tearing down mappings that predate it would make setting a cap an
  outage of its own.

### Changed

* **Binary API 0.5.0** adds `bpa_limits_set` — a message of its own rather than
  fields on `bpa_pool_add_del`, so that capping one infected subscriber does
  not recreate the pool and disconnect its neighbours
  ([#69](https://github.com/andrediashexa/hexarouting/issues/69)). Measured in
  the lab: 25 sessions before the commit, 25 after.

## 0.11.0 — the ticket that does not look like NAT

A subscriber on PPPoE has 1492 bytes of usable MTU and nothing tells the far
end. The remote sends full-sized segments, they are dropped where the path
narrows, and path MTU discovery — which exists for exactly this — is routinely
broken by somebody filtering ICMP. What reaches support is **"some sites do not
load"**: not slowly, they hang, and it looks nothing like an MTU problem to
anyone taking the call.

### Added

* **TCP MSS clamping** ([#19](https://github.com/andrediashexa/hexarouting/issues/19)):

  ```
  cgnat pool residential
   tcp-mss 1400
   exit
  ```

  or `tcp-mss auto`, off by default, and `bpa tcp-mss` changes it on a running
  pool. Applied to `SYN` **and** `SYN/ACK`, because the option announces what
  the *sender is willing to receive*: clamping the subscriber's `SYN` limits
  what the internet sends back, and clamping the server's `SYN/ACK` limits what
  the subscriber sends out. Doing one leaves half the path unprotected.

  `auto` resolves to the **smallest** IPv4 MTU among the CGNAT's interfaces,
  less 40 — not the egress one, because the node clamps *before* the forwarding
  lookup and does not yet know which interface the packet will leave by. The
  minimum errs safely: a clamp smaller than necessary costs throughput, a
  larger one protects nothing. Recomputed as interfaces and MTUs change, and
  `show bpa pool` prints the resolved number rather than the word.

  It never raises an MSS that is already smaller, never inserts an option into
  a `SYN` that has none, never touches a segment outside the handshake, and
  refuses values below 536 (RFC 1122 §4.2.2.6) or above 9000 — a typo such as
  `tcp-mss 14` would otherwise break every connection through the box.

* **`lab/mss.sh`**, which reads every value off a **captured packet** and
  asserts both checksums on the same bytes. A wrong checksum fails silently:
  the peer drops the segment, the connection hangs exactly as it did before,
  and the counter here says it was fixed. `sh tools/lab.sh mss off` fails the
  six checks that claim a clamp was applied.

### Changed

* **The rules now say that the issue is the record of the work**
  (`CONTRIBUTING.md` §2). A design decision the issue did not anticipate, a
  measurement and its conditions, a limit the change deliberately does not
  cross, and anything found on the way that belongs to a different issue all go
  back to the issue as they happen; closing carries the acceptance criteria,
  ticked, with what the evidence was. An issue closed with "done" is an issue
  whose reasoning was thrown away.
* **Binary API 0.4.0** changes the layout of `bpa_pool_add_del` and
  `bpa_pool_details`. A client built against 0.3.0 must be rebuilt.
  `lab/api.sh` pins that shape and is what caught it.

### Known, and filed rather than hidden

* `bpa tcp-mss` changes the clamp **in place** and keeps every session; doing
  the same through the VTY replaces the pool and drops them all. That is true
  of every per-pool setting today and is
  [#69](https://github.com/andrediashexa/hexarouting/issues/69). The caveat is
  in `docs/architecture.md`, because it is worth knowing before answering a
  support call with a commit.

## 0.10.0 — the check that keeps the log from being confidently wrong

A packet arriving on an inside interface with a source the sender does not own
is translated like any other, takes a port out of that address's block, and is
attributed to that subscriber by arithmetic for as long as anyone cares to ask.
The record is indistinguishable from a true one. This release adds the check
that refuses it, and — just as deliberately — a test that says where the check
stops.

### Added

* **uRPF on an interface** ([#24](https://github.com/andrediashexa/hexarouting/issues/24)),
  in the committed configuration, **off by default**:

  ```
  interface ens19
   urpf strict
   exit
  ```

  `strict` requires the source to route back out the interface the packet
  arrived on; `loose` only that it route somewhere. Off by default because
  turning it on where return paths are asymmetric drops traffic that was being
  forwarded correctly a moment earlier, with no symptom except that some things
  stop — a decision that belongs to whoever knows the topology.

  The check itself is **not reimplemented**. VPP's `ip4-rx-urpf-strict` and
  `ip4-rx-urpf-loose` already run ahead of `bpa-in2out` on the same arc, so a
  refused packet never reaches the translator: no port is taken from the block
  it was aimed at and no session exists to be attributed to anyone. What was
  missing was that nothing here could turn it on **as configuration** — it
  could be set with `vppctl` and lost at the next reboot, appeared in no
  running configuration, and no `commit` or `rollback` covered it.

* **`show bpa urpf`** — one line per interface, `<name> <mode>`. VPP answers
  this per interface in a page of human-shaped text, and the VTY reads it back
  on every commit to know whether its own change took effect, because `vppctl`
  exits 0 having printed a parse error.

* **`lab/urpf.sh`**, which measures the **harm first**: with nothing enabled, a
  subscriber's address injected on a second inside interface — where its route
  does not point — is translated and creates a session. Only then is the check
  turned on. `sh tools/lab.sh urpf off` fails the six checks that claim uRPF
  does anything.

### Where it stops, and why that is a test

**One subscriber spoofing its neighbour is not caught.** Every subscriber of a
pool is reachable out the same inside interface, so the FIB answers "yes, that
way" for the victim's address exactly as it does for the attacker's. The lab
sends a neighbour's address on the right interface and **requires** it to be
translated, so anyone who later believes uRPF closes that hole is contradicted
by a failing test rather than by a paragraph. Closing it needs the address
bound to the subscriber it was issued to
([#21](https://github.com/andrediashexa/hexarouting/issues/21)), or the access
network refusing the spoof before the packet arrives.

The two failures are also counted apart, which the lab asserts:
`ip4-rx-urpf-strict: uRPF Drop` is a spoof or a misconfiguration;
`bpa-in2out: no pool for source address` is an address this box does not
translate, which is ordinary on a shared link.

## 0.9.0 — an answer for the customer who needs a port in

The answer to a subscriber who needs to be reached from outside was "buy a
public address" — a lost sale exactly when the provider has none, which is the
reason the CGNAT is there. There is a table for it now, and the whole of this
release is about making that table impossible to contradict.

### Added

* **Static port forwarding**
  ([#17](https://github.com/andrediashexa/hexarouting/issues/17)). One inbound
  port pinned to one subscriber's socket, per pool:

  ```
  cgnat pool residential
   forward tcp 6200 to 100.64.0.5 80
   exit
  ```

  and, on a box already carrying traffic and without redefining the pool,
  `bpa forward residential tcp 6200 to 100.64.0.5 80`. Also `show bpa forward`,
  and `bpa_forward_add_del` in binary API **0.3.0** — a message of its own,
  because `bpa_pool_add_del` already ends in its variable-length prefix array
  and because a forward has to be addable to a pool that is carrying traffic.

  The operator states the public **port** and never the public **address**. The
  address is the subscriber's own, the one the pool's arithmetic assigns it: a
  subscriber receiving on one address and sending from another is not paired
  (RFC 4787 REQ-2), and `bpa_reverse()` recovers the subscriber from
  `(public address, port)` with no lookup, which is what makes the return path
  shard and the deterministic mode auditable. A forward anywhere else would be
  a mapping the formula cannot produce, and every later reader of the
  configuration trail would compute the wrong subscriber for exactly those
  ports.

  So the port **MUST** be inside that subscriber's own block, and one that is
  not is refused naming the subscriber whose block it would have taken:

  ```
  public port 2048 belongs to 100.64.0.9's block, not to 100.64.0.5's.
  100.64.0.5 holds 200.150.0.17:1536-2047
  ```

  **Deterministic pools only.** In dynamic mode a block belongs to whoever
  holds it now and is released when that subscriber goes idle, so a pinned port
  would follow the block to the next customer — still working, reaching
  somebody else, which is worse than not working. Refused with that reason, and
  tracked as [#67](https://github.com/andrediashexa/hexarouting/issues/67).

  The port is **reserved, not checked**: it is marked used in the subscriber's
  bitmap when the block is created, so the allocator is never told it exists
  and no allocation path — port preservation, an ALG's media pair, an ordinary
  new flow — can reach it. A test on each of those paths would have been three
  places to remember instead of one. Two consequences follow: the bitmap has no
  protocol dimension, so a port forwarded for TCP is reserved for UDP as well;
  and a forwarded session does not hand its port back when it expires, because
  the port belongs to the configuration rather than to the session.

* **`lab/forward.sh`**, and `sh tools/lab.sh forward off` runs it with no
  forward configured, where **ten** of its twenty checks must fail. One of them
  passed in both modes at first — the session from an earlier section still
  held the port — so the lab now closes that session and asserts the table is
  empty before the forward is configured.

### Fixed

* **A forward stated on a `bpa pool` line reached the dataplane with a zero
  public address.** Validation *completes* a forward as well as checking it —
  the public address and the port offset are derived there and nowhere else —
  and it was validating a copy and storing the original. It reserved nothing,
  translated nothing, and appeared in `show bpa config` exactly as if it
  worked.
* **`/var/log/hexarouting` was created by no installer.** The plugin opens the
  configuration trail with `O_CREAT` and cannot create the directory above it,
  so the trail existed in every lab — where the lab happens to make the
  directory — and on no installed box. In deterministic mode that trail is the
  record of which pool definition was in force at a given instant, which is
  what makes an answer to a court order defensible.

### Changed

* **The configuration trail records a pool's forwards**, and `forward-add` and
  `forward-del` are recorded like any other pool change. A forward is the one
  part of a deterministic pool the formula cannot reproduce; a trail that
  omitted it would answer for that port with the subscriber the formula names,
  who is the wrong one.
* **`docs/roadmap.md`**: the release table said v0.6.0 through v0.8.0 would be
  inbound features, compliance and HA. They were the appliance, its installer,
  SSH, accounts, SNMP and the first performance measurement. The table now says
  what happened and renumbers what has not.

### Not in this release

* **PCP** ([#32](https://github.com/andrediashexa/hexarouting/issues/32)) — this
  is the manual table PCP would automate, and #17 was its stated dependency.
  What PCP still has to bring is on that issue.
* **Forwards on dynamic pools**
  ([#67](https://github.com/andrediashexa/hexarouting/issues/67)) — they need
  the block pinned, which is its own change with its own logging obligations.

## 0.8.0 — the first number, and a level of testing that was not testing

Every performance claim in this repository was untested, and the level of the
test suite that says "real hardware, real traffic" had almost certainly never
carried a packet. Both are answered here, and neither answer is the one that
was expected.

### Added

* **`lab/throughput.sh`** ([#28](https://github.com/andrediashexa/hexarouting/issues/28)).
  It does **not** produce a bits-per-second figure — that needs a NIC DPDK
  drives natively, and #28 stays open for it. What it measures is the part that
  does not depend on the NIC: how many CPU clocks a packet spends inside the
  translation nodes, from VPP's own per-node accounting.

  | | clocks/packet |
  |---|---|
  | `ip4-input` | 21–26 |
  | `ip4-lookup` | 20–23 |
  | **`bpa-in2out`** | **456**, now 396 |

  Flat across 64, 128, 512 and 1500 bytes, so a per-packet cost and not a
  per-byte one. At 2400 MHz that is about **25 cores** spent in that one node
  for 100 Gbps of 64-byte frames, before the driver or the IP stack. The target
  stated at the outset was 100 Gbps; this is the first measurement that says
  anything about whether it is reachable, and it says the per-packet cost has
  to come down by a large factor first
  ([#63](https://github.com/andrediashexa/hexarouting/issues/63)).
* **The hardware lab proves the path instead of describing it**
  ([#64](https://github.com/andrediashexa/hexarouting/issues/64)).
  `make hw-up`, `make hw-lab`, `make hw-vpp` — the last one pings and fetches
  over TLS from a subscriber address to the real Internet and checks the public
  address and port block against `ref/bpa_model.py`.

### Changed

* **The hairpin lookup is skipped for destinations that cannot be ours.** Every
  translated packet used to scan the pools asking "is this one of mine", each
  doing a binary search, for a case that almost never happens. A two-compare
  window over all public addresses answers "definitely not" first: **456 → 396
  clocks per packet**. It is a superset test — never "yes", only "no" or
  "maybe" — so it can dismiss a non-hairpin early but cannot hide a real one.

### Fixed

* **The hardware lab never carried a packet.** `hw-lab.sh` flushes the peer's
  address, which deletes the route that sends test traffic into the CGNAT, and
  the script then *printed that route as something to type by hand*. Every run
  destroyed it; following the printed instructions worked and running the lab
  did not; nothing said which.
* **VPP invented a MAC** for the interface and answered ARP with it, so the
  peer sent every frame to an address the NIC drops. The kernel device's MAC is
  used now.
* **`hw-vpp.sh` did not stop the previous VPP.** A second run added the CGNAT
  feature to the same arc twice and every `create` that already existed failed
  quietly — so a change to the script took no effect while it still reported
  success.

### What a green test can hide

Two of these are worth repeating because they are the same mistake in different
places, and the labs only caught them because each was run with the feature
removed:

* **The throughput harness measured a drop path.** The pool was created with an
  outside range overlapping the generator interface's own address, so it was
  refused; the command's output went to `/dev/null`; every packet took a drop
  path; and the table reported the cost of *failing* to translate as the cost
  of translating. It now checks the pool exists and asserts the session count
  grew.
* **A green ping proved nothing in the hardware lab.** With the peer's route
  missing the ping still passes — it leaves through the peer's default gateway,
  something upstream translates it, and it comes back while this CGNAT never
  sees a packet. Session counts are read before and after and the difference is
  asserted.

### Known gaps

Bits per second still needs a real NIC
([#28](https://github.com/andrediashexa/hexarouting/issues/28)). There is no HA
([#6](https://github.com/andrediashexa/hexarouting/issues/6)) and no
authorization in the VTY
([#35](https://github.com/andrediashexa/hexarouting/issues/35)).

## 0.7.0 — the two things an operator does before carrying traffic

Log in as somebody other than root, and point the NMS at it. Both were the
last configuration on the box that `commit` and `rollback` did not cover.

### Added

* **The SSH daemon and the accounts, in the configuration**
  ([#45](https://github.com/andrediashexa/hexarouting/issues/45)). `ssh port`,
  `listen-address`, `permit-root-login`, `password-authentication`, and
  `user <name>` with authorized keys or a crypt-hashed password. Accounts are
  **real POSIX accounts**, not a private database: an appliance that invents its
  own accounts is one whose accounts nobody deprovisions when somebody leaves.
  The login shell is the marker, so there is no separate list to keep in step.
* **Three guards against the lockout**, because SSH is the one configuration
  that can strand an operator on the other side of the country. `commit check`
  refuses a change that leaves nobody able to log in — only when *that change*
  causes it. Any change under `ssh` or `user` is applied as `commit confirmed`
  whether or not it was asked for, and the VTY says so. `sshd -t` validates
  before the daemon is reloaded and the previous file is put back if it
  refuses; it fails **closed**, because refusing a valid change costs a retry
  and accepting an invalid one costs the box.
* **An SNMP agent** ([#46](https://github.com/andrediashexa/hexarouting/issues/46)),
  read-only twice over — the generated `snmpd.conf` carries `rocommunity` and
  `rouser` and never `rwcommunity`, and the agent answers every SET with
  `not-writable`. It serves RFC 4008's `natDefTimeouts`, which map exactly, so
  an existing NMS reads them with no custom template.
* **No stale numbers.** If the dataplane is not answering, the agent serves
  *nothing* rather than what it last saw. A monitoring system that keeps
  reporting yesterday's numbers through an outage hides it; one that reports a
  gap shows it.

### Changed

* `hexarouting.conf` and every rollback revision are now **root-only**, set by
  the installer and again on every write. They carry password hashes — they
  have to, or a rollback could not restore a password.
* `show running-config` prints `password <set, not shown>` and redacts
  community strings and v3 passphrases. A hash on a screen shared during an
  incident is a hash that has left the building.
* The ISO now carries `snmpd`.

### Not delivered, and why

* **The enterprise half of the SNMP MIB is dark until you register a PEN.**
  The numbers an operator actually watches — subscribers holding blocks, ports
  used, subscribers near their limit, refusals, drops — have no standard OID.
  They live under the operator's own IANA Private Enterprise Number, and there
  is no default and cannot be one: an OID under somebody else's PEN is a lie
  about who the object belongs to. Set `snmp enterprise-number` once you have
  it; registration is free and takes days to weeks, and the request names the
  company rather than the software.
* **The SNMPv3 handshake is exercised only as far as configuration** — that the
  user is granted read-only access and created in net-snmp's store. A poll
  authenticated with v3 is not asserted end to end.
* **`IF-MIB` counters for the VPP interfaces are not served.** The kernel does
  not see those NICs, so `snmpd` has nothing to report about them.

### Five defects the labs found

Four in the SNMP agent, one of them the exact trap its own docstring warns
about: `show bpa timeouts` is not a command, `vppctl` exited 0 and printed an
error, and the agent served nothing — indistinguishable from a dataplane that
is down. The request loop mixed file iteration with `readline()`, so every
request returned NONE. The enterprise OID was read from the wrong field of the
`view` line, leaving that subtree silently dark. And the liveness check sat
*inside* the ten-second cache, so the first ten seconds of an outage were
served as though nothing had happened — measured, with SNMP still reporting a
subscriber after the dataplane had been killed.

The fifth: `SnmpDaemon.read()` used `systemctl is-active` as the truth about
whether SNMP was *configured*, which on a system systemd is not managing made
the next commit delete the configuration the previous one had just written.

### Known gaps

Unchanged: throughput has never been measured
([#28](https://github.com/andrediashexa/hexarouting/issues/28)), there is no HA
([#6](https://github.com/andrediashexa/hexarouting/issues/6)), and the VTY has
no authorization — who exists is now configurable, what they may do is not
([#35](https://github.com/andrediashexa/hexarouting/issues/35)).

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
