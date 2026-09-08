# Changelog

## TL;DR

Every merge into `master` is a release: a version bump, an annotated tag and
an entry here. The project stays on `0.x.x` until the maintainer declares it
ready for production, so breaking changes are permitted and are called out
under their own heading. Newest release first.

The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
are to be interpreted as described in
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

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

Nothing merged since v0.22.0.

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
