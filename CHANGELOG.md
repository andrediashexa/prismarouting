# Changelog

## TL;DR

Every merge into `master` is a release: a version bump, an annotated tag and
an entry here. The project stays on `0.x.x` until the maintainer declares it
ready for production, so breaking changes are permitted and are called out
under their own heading. Newest release first.

The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
are to be interpreted as described in
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

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

[#141](https://github.com/andrediashexa/hexarouting/issues/141),
[#172](https://github.com/andrediashexa/hexarouting/issues/172),
[#187](https://github.com/andrediashexa/hexarouting/issues/187),
[#210](https://github.com/andrediashexa/hexarouting/issues/210),
[#213](https://github.com/andrediashexa/hexarouting/issues/213),
[#217](https://github.com/andrediashexa/hexarouting/issues/217),
[#218](https://github.com/andrediashexa/hexarouting/issues/218),
[#219](https://github.com/andrediashexa/hexarouting/issues/219),
[#220](https://github.com/andrediashexa/hexarouting/issues/220),
[#221](https://github.com/andrediashexa/hexarouting/issues/221),
[#222](https://github.com/andrediashexa/hexarouting/issues/222),
[#223](https://github.com/andrediashexa/hexarouting/issues/223).

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

### Fixed

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
  **16 to 21 clocks** against `bpa-in2out`'s own 396, so the read is not the
  expensive part - the mutability is; and letting a worker borrow a block from
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

  57 checks before, **103** after.

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

### Internal

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

[#213](https://github.com/andrediashexa/hexarouting/issues/213).

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
