# Claims sweep, 2026-09-14

A sweep for one defect shape only: **a sentence this product says out loud that
the box does not honour.** Not a bug hunt, not a review. Every entry below was
reproduced before it was written down, and everything that could not be
reproduced was discarded rather than reported.

The shape comes from five findings in two releases, every one of them found on
a real box by a person rather than by anything in this repository:
[#303](https://github.com/andrediashexa/hexarouting/issues/303),
[#305](https://github.com/andrediashexa/hexarouting/issues/305),
[#306](https://github.com/andrediashexa/hexarouting/issues/306),
[#307](https://github.com/andrediashexa/hexarouting/issues/307) and
[#295](https://github.com/andrediashexa/hexarouting/issues/295). What they have
in common is where they live: in the first thing an operator reads, usually
next to something correct.

Swept against v0.74.0. Nothing was fixed *during* the sweep, deliberately: a
sweep that stops to fix is a sweep that stops. **All ten were fixed afterwards
and shipped in 0.74.1**, each with its own issue carrying the measurement and
the three proofs, and every finding below is left as it was written so that
what was found can be read apart from what was done about it.

What the shape turned out to be, which is worth more than the ten:
[docs/roadmap.md](roadmap.md) records it under *Ten things the product said and
did not do*. In one line: a sentence that is true of one box and false of
another, printed by a path that never asked which box it was on.

## What was found

Ten findings, each reproduced, each with its measurement in the issue.

### What a box prints

| | |
|---|---|
| [#308](https://github.com/andrediashexa/hexarouting/issues/308) | `show interface` sends the operator to `show log`, and this VTY has no such command. It is the only occurrence of that spelling in the whole product, and it is printed on the one screen an operator reaches when the box forwards nothing and the binding is already correct. The nearest command it does have, `show logging`, is FRR's system log, which cannot say why DPDK refused a device. |
| [#309](https://github.com/andrediashexa/hexarouting/issues/309) | The boot self-test says the card carrying the default route "is refused, so naming the management one cannot cut it off". Since #303 the VTY accepts exactly that card on a box that has only one. Right on a two-NIC box, wrong on the box everybody first meets. The completion says it too, which is the screen read before typing. |
| [#310](https://github.com/andrediashexa/hexarouting/issues/310) | #305's contradiction is still in a second message. On the installation medium, committing a dataplane interface prints "reboot the box in a window you have chosen, and it comes up forwarding on that card", under a banner saying nothing there survives a reboot. Four places in the VTY ask whether this is a medium; none of them is on the configuration path. |
| [#312](https://github.com/andrediashexa/hexarouting/issues/312) | `commit confirmed` takes any number. The help and the wiki both say `<1-60>`. `600` arms ten hours. `0` arms nothing at all, writes no record, prints no deadline, and answers `commit complete` exactly as a plain commit would. The operator typed the safety-net command and has no net, and nothing on the screen says so. |

### What the documentation promises

| | |
|---|---|
| [#313](https://github.com/andrediashexa/hexarouting/issues/313) | The wiki tells an operator to prove the audit trail was not tampered with, immediately before a step that erases the disk holding the only copy of it. Neither the spelling it prints nor the VTY's own is handed to the dataplane: both go to FRR's parser. The check is reachable only through `start shell`, which is administrators only. The wiki is also printing a dataplane CLI command at an operator, which the wiki's own rule forbids. |
| [#314](https://github.com/andrediashexa/hexarouting/issues/314) | Two operator pages confirm a port forward with `show cgnat pool`, and one concludes "O forward listado no pool". That screen cannot print a forward, and looks identical whether the forward applied or not. The wiki's own reference page, one directory over, names the command that answers. |

### What the process says it does

| | |
|---|---|
| [#311](https://github.com/andrediashexa/hexarouting/issues/311) | The release gate passes a medium whose self-test ends `N check(s) failed. The box is NOT ready.`, and reprints that sentence under `==> ... is ready to publish`. `tools/selftest-verdict.sh` defines two readers for precisely this distinction and the one meaning "fit to hand over" is called by nothing. `lab/install-iso.sh` holds the rule; the boot phase and the gate do not. #307's asymmetry, in the same two files. |
| [#315](https://github.com/andrediashexa/hexarouting/issues/315) | `TESTING.md` says "CI runs level 1 on every commit". There is no CI: no workflow, no YAML of any kind, no run ever recorded. `CONTRIBUTING.md` rests a MUST on `make test`, and that MUST is held by attention alone. |
| [#316](https://github.com/andrediashexa/hexarouting/issues/316) | `TESTING.md`'s first four commands include one that cannot work, and it does not fail cleanly: under `set -eu` it reconfigures the peer machine's NIC over ssh first and dies on the local command two lines later. The working path is in the same file, two hundred lines down. |
| [#317](https://github.com/andrediashexa/hexarouting/issues/317) | Three numbers `TESTING.md` states as measured disagree with the code. "36 labs" is 53. "4 at a time" is a formula off `MemTotal`. The documented ipfix off-run count is one below the floor the suite fails for missing, so if it were true the release suite would be red. |

## What was swept and found nothing

Recorded because a section nobody measured is how #303 happened, and an
unswept area reads exactly like a clean one.

* **Every VTY command quoted inside a message the box prints.** Extracted from
  the AST of `src/prismash/prismash.py`, excluding docstrings, and walked against
  the live command tree in both modes. Thirty-five commands. One was wrong,
  and it is #308. Three more were flagged and traced to the tree accepting
  them by another door: `ip route <prefix> blackhole` and `no interface
  <name>` are legacy spellings the tree deliberately does not offer, and `ip
  ospf mtu-ignore` is an interface-block statement rather than a top-level
  one. `test/test_doc_commands.py` already does this for `docs/`, `wiki/` and
  `README.md`, and passes; it does not do it for the VTY's own messages, which
  is why #308 survived.
* **The same extraction over `appliance/selftest`,
  `appliance/prisma-routing-tune`, `appliance/prisma-routing-install`,
  `appliance/bind-dataplane-nic`, `appliance/apply-tuning`,
  `appliance/wait-for-vpp` and `appliance/install.sh`.** Every VTY command
  those scripts quote exists. The one flagged, `'no workers'`, is a phrase
  from a startup file and not a command.
* **Every absolute path named in a message `prismash.py` prints.** Eleven, all
  of them files an installed box has. This is #295's shape and it is not
  repeated anywhere in the VTY.
* **The installer's three claims about what the boot self-test will say.**
  `prisma-routing-install` promises, at the screen the operator reads before
  walking away, that the self-test says so at every boot when there is no
  management network, prints the DHCP address it was given, and reports a
  records mirror still synchronising. All three were traced to the branch in
  `appliance/selftest` that prints them. All three hold.
* **Every message in the VTY containing the word "reboot".** Twelve. One is
  #310; the rest are correct on both an installed box and the medium.
* **The #303 remedy path end to end.** `bind-dataplane-nic`'s one-NIC branch,
  `dataplane_if_refusal`, `management_handover` and `take_management_card`
  were driven against fake one-NIC and two-NIC trees. The mechanism is right.
  What is wrong is what two other screens say about it, which is #309.
* **The #306 fix.** `Shell.forwarding_count()` is a single holder read by all
  three views, and the note under the count names what is excluded. No new
  instance of that shape was found.

## How it was run

Every finding was produced by running the code or reading the file, and the
output is quoted in the issue rather than described. Where a code path needed a
box, it was driven against a fake tree through the seams the product already
has - `SYS_CLASS_NET`, `PROC_NET_ROUTE`, `TUNE_ENV`, `PRISMA_LIVE_SQUASHFS` -
which is the same technique `test/test_dataplane_if.py` uses and for the same
reason.

No lab was run, no container was started and nothing was merged: the full lab
suite was running on another tree throughout, and a lab started beside it
returns a false verdict. That is a real limit on what this sweep can claim, and
each issue says in its own words which half of it was measured and which half
was read.

## What this sweep could not reach

* **No medium was booted and no box was installed.** Three of the ten findings
  (#310, #311, #316) are about behaviour on a booted medium or a running lab,
  and in each case what is measured is the code path and the absence of the
  guard, not the console. Each says so.
* **Nothing was measured on a real appliance.** #303, #305, #306 and #295 were
  all found on one. That is still the surface nothing in this repository
  covers, and it is the reason this sweep leant on the places a box's words
  can be checked without a box.
* **`docs/wiki-audit.md` and
  [#277](https://github.com/andrediashexa/hexarouting/issues/277)** already
  record that the wiki mirror stands ahead of what operators can read, which is
  a different question from whether the mirror is right.
* **The three large engineering documents were not read end to end.**
  `docs/architecture.md`, `docs/routing.md` and `docs/logging.md` were swept
  only where they make a claim about the box or the process. **That gap was
  closed on 2026-09-15**; what was found and what is still out of reach is the
  section below.

# The three large documents, 2026-09-15

The 7,236 lines the sweep above named and did not read: `docs/architecture.md`
(2,127), `docs/routing.md` (4,239) and `docs/logging.md` (870), against the
code. Swept against v0.74.2. Same rule as above - every finding reproduced
before it was written down, and a sentence that merely looked wrong was
discarded. Unlike the sweep above this one **did** fix as it went: these are
documentation defects with no behaviour behind them, so the fix is the same
edit as the finding and a second pass would only be a second chance to
mistranscribe it.

## How it was checked

Mechanically first, so that the reading had something to argue with:

| What was walked | How | Found |
|---|---|---|
| Every backticked VTY command in prose, both modes | 149 of them through `Shell._options()`, the same walk `?` answers from | every one reaches a tree, FRR, the dataplane CLI or Linux; nothing dead |
| Every `?` listing the pages print | the option sets compared name by name and description by description | **two wrong**, below |
| Every markdown link and anchor | resolved against the file and its headings | all resolve |
| Every backticked identifier and dotted reference | 183 of them against the whole tree | all present |
| The reserve's cost under each policy | `ref/bpa_model.py` re-run | **one row wrong**, below |
| `show hardware`, all four of its states | driven against `/sys` trees in a tempdir through `SYS_CLASS_NET`, `PROC_NET_ROUTE`, `STARTUP_CONF`, `TUNE_ENV` | **three transcripts wrong**, below |
| The quoted refusals and warnings | typed at the VTY and compared | every one word for word |
| The RFC floors, the IPFIX record's seven fields, the marker's 16 bytes and 30 s, the spill's 1 MB, 4739/512/20 s, the MTU range, the rollback depth, the banner's six lines and 59 columns, `CONSOLE_COLUMNS`, the 16-character marker and the 59-column budget, the off-run counts of five labs, the four `VNET_FEATURE_INIT` blocks, `ref/check_magic.py`'s 1074 divisors at 61 bits | read from the code that owns each, or run | all hold |

## What was found

Five, all in the same two places: a transcript nobody re-took, and a number
nobody recomputed.

| | |
|---|---|
| **A virtio guest is told it has no NIC to choose from.** `routing.md` said a virtio guest's NICs fail the PCI test and printed a `show hardware` message saying so. `pci_address_of()` **walks up** the sysfs parent chain for exactly this reason: the `device` link on a guest points at the virtio bus device whose parent is the PCI one, and reproduced in a tempdir, `/sys/class/net/x/device -> .../pci0000:00/0000:00:12.0/virtio3` resolves to `0000:00:12.0`. The card is eligible and has been since [#286](https://github.com/andrediashexa/hexarouting/issues/286); the box's own message now says so in as many words. The page carried the pre-#286 reading in two places, and a VM with one virtual NIC is, by this page's own sentence, how most people first meet this product. |
| **The first screen of a fresh appliance sends the operator to an editor.** `routing.md`'s `show hardware` transcript quoted `Set PRISMA_DATAPLANE_IF in /etc/prisma-routing/tune.env ... then restart prisma-routing-tune and vpp`. Driven against a `no-pci` startup file, the VTY prints its own remedy instead - `configure`, `dataplane interface <name>`, `commit` - which is precisely what [#283](https://github.com/andrediashexa/hexarouting/issues/283) replaced that advice with. The section is *about* #283 and was quoting the defect. Its driver rows and its column widths had drifted with it. |
| **`show cgnat ?` is missing an entry.** The page lists eleven sub-commands; the tree offers twelve. `ha` has been there since the HA comparison was built and the transcript was never re-taken. |
| **An interface block's `vrf` description is stale**, in both of the two places `routing.md` prints that listing: the box now adds *"; 0 takes the interface back out of one"*. |
| **The reserve's cost under `blocked` is wrong in both numbers.** `architecture.md` said "738 of 882 subscribers moved ... plus the 21 the truncation dropped". Re-running the model it cites gives **750 and 7**. The 7 is also what the `interleaved` row beside it derives from the pool definition - `outside.count x reserve`, 7 x 1 - so the table contradicted itself, and the half that was wrong is the one an operator sizes a `commit remap` window against. |

One more, smaller and in the same shape: `architecture.md`'s memory table said
the dynamic allocator costs **17 bytes per block per worker slice** across four
arrays. `bpa_dyn.h` carries **five** - `free_pos` was added so a named block
comes out of the free stack by index rather than by a walk - and the code's own
arithmetic beside it says a 258048-block slice "grows from about 4.4 MB to
5.4 MB", which is 17 bytes to **21**.

All six were documentation defects and all six were fixed. **No product defect
was found in these three documents**: where a page and the code disagreed, the
code was right every time.

## The reader that was added

Two of the six are a truth living in two places with nothing comparing them, so
`test/test_doc_copies.py` gained a reader for each rather than only the fix:

* **A `?` listing a document prints, against the tree that answers it.** The
  option names *and* their descriptions, out of `Shell._options()`.
  `test/test_doc_commands.py` walks the **command** on the prompt line and
  stops at the placeholder; what follows is output, and nothing read it. Shown
  red four ways: the `ha` row removed, the `vrf` description put back, an
  option the tree does not offer added, and the prompt line reworded so the
  check would otherwise have gone quiet.
* **The reserve's cost, against the model that is its definition.** The table's
  four numbers recomputed from `ref/bpa_model.py` - run, not grepped out of
  `ref/validate.py`'s output - plus the agreement between the two rows about
  how many the truncation drops. Shown red with the document's old numbers put
  back.

## What this sweep could not reach

* **Nothing was run that needs a dataplane, a NIC or a booted medium.** The
  full lab suite was running on another tree throughout, so no lab and no
  container was started, and the C suites were not built. That puts these out
  of reach: the `MUST`s `lab/ipfix-outbuf.sh` holds over the TCP spill; the
  paired-address rates `test/test_bpa_dyn.c` asserts (100%, 99.6%, 99.2%),
  which agree with that file's own comment and were read rather than measured;
  `bpa_pool_guaranteed()` against `test/test_bpa_pool.c`'s simulation; and
  every `lab/*.sh` assertion total the pages quote. The five off-run counts
  those pages state (`forward` 26, `esp` 25, `pptp` 17, `ftp-alg` 13,
  `rtsp` 11, `sip` 7) were checked against the `OFF-RED-AT-LEAST` floors the
  labs declare, which is a number against a number and not a run.

  Half of that reach closed on **2026-09-15**, when the C suites were built and
  swept - see `docs/roadmap.md`. The paired-address rates are measured rather
  than read: `test_bpa_dyn.c` prints 100% for serial arrivals, 99% for parallel
  and 99% for churn at their worst cells, which are the floors it asserts, and
  `bpa_pool_guaranteed()` is compared against the simulation in
  `test_bpa_pool.c` over every shape it enumerates. The labs are still out of
  reach and for the same reason, so the `lab/*.sh` totals above stand as
  written.
* **"Measured over 2800 combinations of blocks, inside addresses and worker
  count"** in `architecture.md`'s re-partitioning bullet is a measurement whose
  code is in no file in this repository. It is a candidate and was **not
  reproduced**; reproducing it needs the simulation to be written down
  somewhere a reader can re-run, which is the point.
* **Everything these pages record as measured at a live FRR 10.7.1 or a live
  VPP 26.06** - the read-back table, the route-map and prefix-list verdicts,
  the RPKI cache markers, the QinQ three-tag over-match, the router
  advertisement intervals - was left as written. Each says where it was
  measured; none could be re-measured here.
* **The wiki was not touched**, and is a different question: see
  `docs/wiki-audit.md`.
