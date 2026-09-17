# Prisma Routing - Sweep of the three documents nobody had read end to end

## TL;DR

`docs/architecture.md`, `docs/routing.md` and `docs/logging.md` are 7,126 lines
that no sweep had read whole. They were read whole, and every number, command,
C symbol, VPP node name, struct field and environment variable in them was
checked against the tree.

Five statements do not hold:
[#318](https://github.com/andrediashexa/hexarouting/issues/318) (a refusal the
box stopped printing that way),
[#319](https://github.com/andrediashexa/hexarouting/issues/319) (two counts,
each repeated in a test comment beside an assertion that disagrees) and
[#320](https://github.com/andrediashexa/hexarouting/issues/320) (a volume
figure that contradicts the table under it, and a suite count that is two
thirds of the suite).

**Everything else held.** That is the larger half of the result, and it is
recorded below by section rather than as a sentence, because a section nobody
names is a section nobody knows was read.

## 5W2H

| | |
|---|---|
| **What** | A record of what was read in the three documents, what did not hold, and what did. |
| **Why** | The previous sweep closed ten defects and wrote down that these three files were the gap in its own coverage. A gap somebody recorded and nobody closed is how the eleventh ships. |
| **Who** | Whoever edits one of those pages next, and whoever wants to know whether a claim on it was ever checked. |
| **When** | Against the tree at `chore/docs-claims-sweep`. Re-run it when a page changes shape, not on every edit. |
| **Where** | `docs/architecture.md` (2,063 lines), `docs/routing.md` (4,194), `docs/logging.md` (869). |
| **How** | Every claim was established from the product: symbols and node names grepped in `src/`, numbers read from the constant that defines them, arithmetic run, and `ref/check_magic.py` and `test/test_english_only.py` executed. No lab was run and no container was started. |
| **How much** | Five findings, three issues, and eleven classes of claim that held. |

## 1. What did not hold

| # | Where | What the document says | What the code does |
|---|---|---|---|
| [#318](https://github.com/andrediashexa/hexarouting/issues/318) | `routing.md:3489` | the undefined-route-map refusal is printed "as one line of 221 columns", and the width section "says why it was left alone" | `Shell.say_finding()` prints four rows, widest 77, inside the 79-column budget. The section it points at is the one that made that change |
| [#319](https://github.com/andrediashexa/hexarouting/issues/319) | `routing.md:3946` | "four of the twenty-five statements of the provider read-back above are marked" | three of twenty-four, which is what `routing.md:1347` and the fixture's own assertion say |
| [#319](https://github.com/andrediashexa/hexarouting/issues/319) | `routing.md:4033` | `neighbor 2001:db8:aaaa:bbbb:cccc:dddd:eeee:ffff route-server-client` "at 66" columns | 67 |
| [#320](https://github.com/andrediashexa/hexarouting/issues/320) | `logging.md:7` | "~2.4 GB/day for per-flow NAT" | the Volume table at `logging.md:326` says ~1.6 GB from the assumptions the same page states |
| [#320](https://github.com/andrediashexa/hexarouting/issues/320) | `logging.md:501`, `:588` | "all 38 suites" | `test/suites.list` holds 54, and held 39 at the commit that wrote the first of the two |

Four of the five are numbers. A number either matches or it does not, which is
why they are the findings a sweep of prose can be held to.

## 2. What held

Named by class, so that a reader can tell what was measured from what was
merely read past.

**Every C symbol, node name and struct field.** Twenty-two symbols named in
`architecture.md` alone resolve in `src/plugins/bpa/`, including
`bpa_reverse_sub`, `bpa_pool_settings_set`, `bpa_alg_reserve_pair`,
`bpa_sub_key` and `bpa_seq_adj_t`. The VPP node names resolve too, including
`bpa-expire-process` and the four `VNET_FEATURE_INIT` blocks, all four of which
carry the `runs_after` on `ip4-sv-reassembly-feature` that `architecture.md`
requires of them.

**Every `vppctl` command.** All twenty-nine `VLIB_CLI_COMMAND` paths in the
plugin were listed and compared against what the three documents name.
`bpa stop-drain`, `bpa audit-path`, `bpa logging tcp` and
`show bpa pool history` all exist, and the VTY spellings beside them
(`nat cgnat logging udp ... src ...`, `show cgnat logging`) are in `hexash`'s
grammar.

**Every Python seam and environment variable.** `Shell.SYS_CLASS_NET`,
`Shell.PROC_NET_ROUTE`, `Shell.STARTUP_CONF`, `Shell.TUNE_ENV`,
`Shell.PCI_DEVICES`, `Shell.PCI_DRIVERS` and
`Shell.why_no_dataplane_interface()` are where `routing.md` says they are, and
so are `HEXA_DATAPLANE_IF`, `HEXA_DATAPLANE_MGMT`, `--keep-management` and
`/etc/hexarouting/confirm-pending` (the last three were removed by #385 since
this sweep; `routing.md` no longer names them).

**Every timeout and limit.** The four RFC floors in `architecture.md:888-891`
are the X-macro in `bpa_pool.h:109-119`, value for value and reference for
reference. So are the half-open twenty seconds, the four-second port hold-down,
the 536 and 9000 MSS bounds, the ten points of alarm hysteresis, the 16 MB
session tables, the 17 bytes of per-block ownership, the 2^28 capacity cap and
the `BPA_SIP_MAX_MEDIA` of four.

**Every off-run count.** `architecture.md` states how many assertions drop when
an ALG is switched off, five times. All five match the `OFF-RED-AT-LEAST`
header of the lab they name: ftp-alg 13, rtsp 11, sip 7, esp 25, forward 26.
So do the three assertion totals it states beside them (18, 18, 31).

**The reference model.** `ref/check_magic.py` was run. It prints
`checking 1074 divisors, from 1 to 266,583,982`, `worst-case intermediate
product: 61 bits`, which is `architecture.md:110-111` word for word.

**The IPFIX record.** The seven information elements and their widths in
`logging.md:58-64` are `bpa_block_elements[]` in `bpa_log.c:30-38`, in order.
The boot marker's two elements, its 16 bytes and its 30-second interval are
`BPA_LOG_MARKER_RECORD_SIZE` and `BPA_LOG_MARKER_INTERVAL`.

**The verifier's own words.** The three tamper verdicts in `logging.md:758-760`
are the three `vlib_cli_output` calls in `bpa_audit.c:559`, `:574` and `:648`,
and `no chained entries: nothing here is covered` is at `:634`.

**The SNMP surface.** Six `natDefTimeouts` objects and six enterprise objects,
in the order `routing.md:2279-2286` gives them, are `_TIMEOUT_OIDS` and
`_ENTERPRISE_OBJECTS` in `appliance/hexarouting-snmp-agent`.

**The console arithmetic that is right.** `UNCHECKED_MARK` is sixteen
characters; `neighbor 2001:db8:aaaa:bbbb::1 soft-reconfiguration inbound` is
fifty-nine; the marker it replaced put that line at eighty-nine; the budget is
79 less four less sixteen. The banner is six lines and fifty-nine columns. Only
the one comparison in `#319` is off.

**The rollback and confirm constants.** `ROLLBACK_KEEP` is 20 and the usage
says `<0-19>`; the confirm timer is `OnUnitActiveSec=15` with `OnBootSec=60`;
`CONFIRM_ATTEMPTS` is 5; the MTU range is 576-9216 in the grammar and in the
refusal.

## 3. What this sweep did not cover

* **No box, no lab, no container.** Every claim about what the dataplane does
  at run time was checked against the code that does it, not against a box
  doing it. Claims measured against a live VPP or a live FRR and quoted as such
  were taken on the evidence the page gives for them.
* **The two width tables in `routing.md`** (`:4053-4064` and `:4117-4131`)
  state about fifty column counts between them. Two were reproduced. The rest
  need `commit` and `commit check` driven on each fixture.
* **The Portuguese sections of `routing.md`** were read for claims like the
  rest and hold, but the fact that they are in Portuguese at all was not filed.
  `test/test_english_only.py` passes because `docs/` is deliberately outside
  its scope, for a reason its header gives about `docs/wiki-audit.md`. Whether
  that exemption was meant to cover whole sections of another page is the
  maintainer's to say.
* **`docs/appliance.md`, `docs/ha.md`, `docs/bng.md`, `docs/pool-resize.md` and
  `docs/context.md`** were not read end to end. They are the next gap, and this
  file exists so that it is a recorded one.
