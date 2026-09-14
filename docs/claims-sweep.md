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
  the AST of `src/hexash/hexash.py`, excluding docstrings, and walked against
  the live command tree in both modes. Thirty-five commands. One was wrong,
  and it is #308. Three more were flagged and traced to the tree accepting
  them by another door: `ip route <prefix> blackhole` and `no interface
  <name>` are legacy spellings the tree deliberately does not offer, and `ip
  ospf mtu-ignore` is an interface-block statement rather than a top-level
  one. `test/test_doc_commands.py` already does this for `docs/`, `wiki/` and
  `README.md`, and passes; it does not do it for the VTY's own messages, which
  is why #308 survived.
* **The same extraction over `appliance/selftest`,
  `appliance/hexarouting-tune`, `appliance/hexarouting-install`,
  `appliance/bind-dataplane-nic`, `appliance/apply-tuning`,
  `appliance/wait-for-vpp` and `appliance/install.sh`.** Every VTY command
  those scripts quote exists. The one flagged, `'no workers'`, is a phrase
  from a startup file and not a command.
* **Every absolute path named in a message `hexash.py` prints.** Eleven, all
  of them files an installed box has. This is #295's shape and it is not
  repeated anywhere in the VTY.
* **The installer's three claims about what the boot self-test will say.**
  `hexarouting-install` promises, at the screen the operator reads before
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
has - `SYS_CLASS_NET`, `PROC_NET_ROUTE`, `TUNE_ENV`, `HEXA_LIVE_SQUASHFS` -
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
* **`docs/` was swept only where it makes a claim about the box or the
  process.** `docs/architecture.md`, `docs/routing.md` and `docs/logging.md`
  are large and were not read end to end against the code. `docs/wiki-audit.md`
  and [#277](https://github.com/andrediashexa/hexarouting/issues/277) already
  record that the wiki mirror stands ahead of what operators can read, which is
  a different question from whether the mirror is right.
