# For API clients

What each release changes for a program that talks to the box over the
management API (`prisma.v1`, [docs/api.md](api.md)) - written for the web
interface (prisma-web), which vendors the contract and compares this file
against the release it last vendored. Newest first, one entry per release,
including releases that change nothing a client sees (they say so, so a gap
is never a question).

| | |
|---|---|
| **Who** | Written by whoever cuts the release, in the same commit; read by every API client's maintainers. |
| **What** | Per release: the contract's sha256, API-visible behaviour (errors, limits, units, ordering, health), new or changed fields (unit, counter or gauge, reset, sentinels, 0 vs absent), known bugs a client must work around and the release that fixes them, the lab box's version and upgrade windows, and bench results only when they change what a client should do. |
| **Where** | This file. The contract is `api/prisma/v1/prisma.proto` and `api/health/v1/health.proto`; the full reference is docs/api.md; the box's own changes are in CHANGELOG.md. |
| **When** | Every release, at merge time. A release with no entry here is a release this file failed. |

The lab box clients test against is the X520 bench, **10.40.200.166**. An
upgrade there recreates the management container: the API is gone for about a
minute (74 s measured on 0.149.4, from the upgrade command to the service
answering) and every open stream and session ends. Upgrades are announced here
and to the prisma-web session before they run.


The contract before 0.149.2 - ConfigService, OperationalService's reads,
TelemetryService.Subscribe and Show - has no entry here: it is described rpc by
rpc in [docs/api.md](api.md). Which VTY command each rpc reaches, and which
commands no rpc reaches yet, is [docs/api-coverage.md](api-coverage.md),
generated from the VTY's grammar.

## 0.150.10 - BFD's sessions typed and streamed, and in SNMP

* **Contract:** additive (`prisma.proto` sha256 `3c758e3cf29cdcf8cc738e973502cd69fcb07ec93969d83a99bdf26a0c1c5305`).
* **OperationalService** gains `ListBfdSessions` (`show bfd peers`, or one
  `peer`, as fields: state, seconds in it, diagnostics, both ends' timers in
  ms, five counters; the screen in `text`; INVALID_ARGUMENT for a peer that
  is not an address) and `WatchBfd` (a stream: each session as it is, then
  CHANGED / APPEARED / GONE / UNANSWERED events; `interval_seconds` 1-60, 0 is
  2; a down-and-up between two readings is a CHANGED with the down counter
  moved). BFD configuration stays lines through Edit.
* **SNMP:** BFD-STD-MIB (RFC 7331) is served; traps are not
  (`bfdNotificationsEnable` reads false).
* **Lab box:** .166 is on 0.150.9.

## 0.150.9 - BFD configuration can be removed

* **Contract:** unchanged (`prisma.proto` sha256 `8cc49ae67f9d8279a77b0d443d7ad5f6d016ae0518c6b96c000b32256973efa1`).
* **Removing BFD commits (#570):** on 0.150.8 a `no bfd`, or a `no peer`
  inside `bfd`, failed `commit check` with "routing validation failed".
  A `no` of any block now removes its body with it, so removing a peer, a
  profile, a neighbour's `bfd`, a static route's `bfd` or the whole block
  commits, through Edit as at the VTY. The BFD config added on .166 to find
  this was removed; it has none.
* **Lab box:** .166 is on 0.150.8.

## 0.150.8 - BFD, phase 1

* **Contract:** unchanged (`prisma.proto` sha256 `8cc49ae67f9d8279a77b0d443d7ad5f6d016ae0518c6b96c000b32256973efa1`).
* **BFD at the VTY (#559, phase 1):** a `bfd` block (`peer` sessions and
  `profile` timer sets), `neighbor X bfd [profile]` under `router bgp` and
  `bfd [multi-hop] [source] [profile]` on a static route; `show bfd peers`
  and `show bfd peer <address>`. Through the API they are text for now:
  the statements as lines through Edit, the screens through `Show`. Typed BFD reads, typed config and a
  session-state event are phase 2.
* **bfdd starts with the routing container:** a box upgraded to this
  release refuses BFD statements at commit until the routing container
  restarts (a reboot), and says so.
* **Lab box:** .166 is on 0.150.7.

## 0.150.7 - old container images are removed

* **Contract:** unchanged (`prisma.proto` sha256 `8cc49ae67f9d8279a77b0d443d7ad5f6d016ae0518c6b96c000b32256973efa1`).
* **Nothing changes through the API** (#569). A box now removes the
  `prisma-*` images no container uses, keeping the release it is and the
  one before it, after an upgrade and at every boot. The first upgrade to
  this release on a box with many old images takes a little longer.
* **Lab box:** .166 is on 0.150.6.

## 0.150.6 - the rest of the screens typed, the probes streamed

* **Contract:** additive (`prisma.proto` sha256 `8cc49ae67f9d8279a77b0d443d7ad5f6d016ae0518c6b96c000b32256973efa1`).
* **OperationalService** gains (#558, phase 7), each with the VTY's screen
  in `text`: `ListOspfInterfaces` (`show ospf interface`: area, state,
  cost, neighbours, the MTU ospfd advertises beside the dataplane's -
  `dataplane_mtu_known` false for an interface the dataplane has none for -
  and `mtu_stall`), `ListIsisInterfaces` (the circuits, `silent` ones, and
  `punt_known`/`punting`/`osi_drops`), `GetHardware` (the NICs Linux has,
  `default_route` marked, `dataplane_holds`; `dataplane_answered` false
  when that could not be asked), `GetRoutingLogging` (`show logging`: FRR's
  logging per daemon - not GetLogging, the CGNAT log export) and
  `GetSnmpFile` (`KIND_MIB` / `KIND_ZABBIX_TEMPLATE`: the file with the
  box's enterprise number in it, a filename and media type to save it as;
  FAILED_PRECONDITION with the VTY's words when it cannot be made).
  `answered` false is the daemon not answering, never an empty box; FRR's
  `-` reads -1 for a cost or metric and 0 for an MTU.
* **`Ping`, `Traceroute`, `Mtr`** (`ProbeRequest {host, count, source}`)
  stream a `ProbeEvent` per line the tool prints: `line` verbatim, with
  `reply`, `hop` or `mtr_hop` when the line is one (mtr prints its report
  at the end of the run), then a last event with `end` (exit status,
  `stopped_at_deadline`, ping's summary). The VTY's own rules: a host,
  count or source it refuses is INVALID_ARGUMENT in its words; a tool the
  box lacks is FAILED_PRECONDITION naming the package; count 1-100, 0 is
  5; the box stops a tool after 300 s. Cancelling the call stops the probe.
  One probe at a time per account; the other reads answer while it runs.
  `Show` still answers the three as text.
* **Coverage:** `show cgnat ha peer <file>` is the only screen left as
  text (it names a file on the box); docs/api-coverage.md counts 658
  typed and 1 text.
* **Lab box:** .166 is on 0.150.5.

## 0.150.5 - a line FRR refuses fails the VTY batch

* **Contract:** unchanged (`prisma.proto` sha256 `e872dea7d1a4fa7c9377183ff2be01bef1b2617f6f5369a78a6579a1d2782f5c`).
* **Nothing changes through the API** (#568): `prismash -c` on the box now
  exits non-zero when FRR refuses the line it relays (`% Unknown command`),
  which matters only to a client running the VTY over ssh and trusting the
  exit status.
* **Lab box:** .166 is on 0.150.4.

## 0.150.4 - the VTY's exit status trusts good screens

* **Contract:** unchanged (`prisma.proto` sha256 `e872dea7d1a4fa7c9377183ff2be01bef1b2617f6f5369a78a6579a1d2782f5c`).
* **Nothing changes through the API** (#567): `Show` returns the screen
  text, which was always right. What changed is the exit status of
  `prismash -c` on the box, which read 1 for the correct `show cgnat
  identity` and `show cgnat history verify` screens; a client that runs
  the VTY over ssh and trusts that status is the one affected.
* **Lab box:** .166 is on 0.150.3.

## 0.150.3 - the rest of show cgnat, typed; SetConfig on an interface works again

* **Contract:** additive (`prisma.proto` sha256 `e872dea7d1a4fa7c9377183ff2be01bef1b2617f6f5369a78a6579a1d2782f5c`).
* **OperationalService** gains ten reads (#558, phase 6), each the VTY's
  screen as fields, with the screen in `text`: `LookupTranslation`
  (public address and port to its subscriber - IPv4, or a NAT64 /56 -
  pool, block, `source` FORMULA/RESERVE/DYNAMIC/OUTSIDE_RANGE and
  `now_only`; INVALID_ARGUMENT for a bad address or port, NOT_FOUND for
  an address no pool owns), `ListTop` (`count`, 0 = 10), `GetDrops`,
  `GetCgnatMemory` (bytes; `sessions_known`, `given`), `ListBlocks` (per
  worker and total), `GetIdentity` (never an identity), `VerifyHistory`
  (a `Verdict` enum), `ListAlgs`, `ListForwards` (`pool` filter) and
  `GetHaStatus` (`agree` only when the peer copy was read and nothing
  diverges). Units and counter/gauge per field in the contract. An
  account gets what the VTY gives it: one that cannot reach the
  dataplane is UNAVAILABLE, as at the prompt. `show cgnat ha peer <file>`
  stays text through `Show`: it names a path on the box's disk.
* **Fixed, both visible to a client:** `SetConfig` with an
  `interface` failed with UNKNOWN on 0.150.2 (pref64 could not be
  pickled) - it works again; `GetSubscriber.blocks` was always empty -
  it now lists the subscriber's blocks.
* **Lab box:** .166 is on 0.150.2.

## 0.150.2 - PREF64: a subscriber's CLAT learns the NAT64 prefix

* **Contract:** additive (`prisma.proto` sha256 `658df1eabeb46a8ea8ce45dc5baab0f238f9266f74dc0eca797c8c20dda96714`).
* **`InterfaceConfig.pref64`** (field 16, repeated string): the NAT64
  prefixes the interface's router advertisements carry as PREF64 (RFC 8781),
  `ipv6 nd nat64 <prefix>` at the VTY, RFC 5952 form, sorted. An empty list
  is no PREF64. SetConfig refuses one on an interface whose
  `router_advertisements` is false, and a prefix no NAT64 pool on the box
  translates, with the VTY's commit-check sentence. GetSchema offers the
  statement; docs/api-coverage.md lists it.
* **What the box does with it:** on such an interface FRR sends the
  advertisements (VPP's own has no PREF64); a UI shows nothing different,
  and `show running-config` keeps the lines under the interface.
* **Lab box:** .166 is on 0.150.1.

## 0.150.1 - FTP through a NAT64 pool

* **Contract:** unchanged (`prisma.proto` sha256 `7905cf993bf993f40ec0d2026697ac216719ff16861cdc41e232d7cbec357ea2`).
* **An ALG on a NAT64 pool (#485, phase 5):** SetConfig now accepts
  `alg ftp on` on a pool whose `prefix_inside` is IPv6 (FTP64, RFC 6384; off
  by default there). `rtsp`, `sip`, `pptp` and `esp` on such a pool are
  refused for good, each with its own commit-check sentence, which replaces
  0.150.0's "an ALG (for now)". A UI offering the ALG toggles on a NAT64 pool
  should offer only FTP; the refusal text is the VTY's and can be shown as is.
* **Lab box:** .166 is on 0.150.0.

## 0.150.0 - NAT64, and the 0.149.x patches proved by a fresh install

* **Contract:** additive (`prisma.proto` sha256 `7905cf993bf993f40ec0d2026697ac216719ff16861cdc41e232d7cbec357ea2`): the NAT64 fields below.
* **`operator` on a fresh install:** a box installed from the 0.150.0 medium
  has the `prisma-operator` group from the start, so granting `operator`
  works the same on a new box as on one upgraded by patches. Nothing for a
  client to change.
* **Proved on a fresh install:** the 0.150.0 gate installs from the ISO and
  checks that `system dataplane restart` (the same path as
  `RestartDataplane`) and a killed dataplane both come back configured, and
  that the log container runs.
* **Lab box:** .166 is on 0.149.33.

### NAT64 (#485), phases 1-4, new in 0.150.0

* **Contract:** additive. `prisma.proto` sha256 `7905cf993bf993f40ec0d2026697ac216719ff16861cdc41e232d7cbec357ea2`.
* **`PoolConfig`** gains 25 `subscriber_length` (0 on an IPv4 pool) and 26
  `nat64_prefix` ("" is the well-known `64:ff9b::/96`). A pool whose
  `prefix_inside` is IPv6 is a NAT64 pool; GetSchema offers both statements,
  and `prefix-inside` takes either family. `mode` may be `dynamic` on one.
  Every other pool field works on one; SetConfig refuses `bypass` (for good)
  and an ALG (for now) with the VTY's own commit-check sentence.
* **`PortForwardConfig.private_address`** on a NAT64 pool is an IPv6 host of
  one of its subscribers' prefixes; on an IPv4 pool it is IPv4 as before.
* **`LogEvent.subscriber`** (WatchLogging) is a NAT64 subscriber's delegated
  prefix, `2001:db8:1200:ab00::/56`, where it was always an IPv4 address: a
  client that parses it as one must take either form.
  `WatchLoggingRequest.subscriber` may be any IPv6 address inside such a
  prefix. No field moved; the comments say it.
* **`Clear`** with `TARGET_CGNAT_SUBSCRIBER` takes an IPv6 address: a NAT64
  subscriber, by any address in its prefix.
* **Dataplane API:** `bpa.api` 0.19.0 adds two messages,
  `bpa_nat64_pool_add` (with its VRFs and reserve) and
  `bpa_nat64_forward_add_del`; no existing layout moves.

## 0.149.33 - a restart that came back is reported as one

* **Contract:** unchanged (`prisma.proto` sha256 `3f8ac0fc80f46252af5a36829b142201d6054af82f0ac0254993f2bd7e4268c8`).
* **Known bug on 0.149.32:** `RestartDataplane` can answer
  `FAILED_PRECONDITION`, `REASON_APPLY_FAILED` ("the saved configuration did
  not load onto the restarted dataplane") while the box actually came back
  whole - the load only failed while FRR was still starting, and loaded a
  few seconds later. **Client workaround on 0.149.32:** after such an
  answer, confirm with `GetVersion` / `GetSystem` (and `GetRunning`) before
  showing a failure.
* **Fixed (#564):** from 0.149.33 the restart waits for the configuration to
  load, retrying for up to 180 s, and reports failure only when it never
  does. `RestartDataplane` can therefore take up to about four minutes to
  answer; set the call's deadline accordingly (the server's own wait is
  300 s).
* **Lab box:** .166 is on 0.149.32.

## 0.149.32 - a dataplane restart keeps the cards' names

* **Contract:** unchanged (`prisma.proto` sha256 `3f8ac0fc80f46252af5a36829b142201d6054af82f0ac0254993f2bd7e4268c8`).
* **Fixed (#564):** on 0.149.30, `RestartDataplane` ran to the end but could
  bring the cards back under kernel names (eth0...), leaving the box with no
  configuration - unreachable when it is managed through a dataplane
  interface. From 0.149.32 the cards keep their names across the restart.
* **The answer says when the configuration did not load:** a restart whose
  saved configuration is refused afterwards now ends `FAILED_PRECONDITION`,
  `REASON_APPLY_FAILED`, its output naming it ("the saved configuration did
  not load onto the restarted dataplane"), instead of success.
* **Do not call RestartDataplane before 0.149.32** on a box managed through a
  dataplane interface.

## 0.149.31 - a test suite cleans up after itself

* **Contract:** unchanged (`prisma.proto` sha256 `3f8ac0fc...68c8`).
* **Behaviour:** none an API client sees: a build-machine fix (#566).
* **Lab box:** .166 is on 0.149.30.

## 0.149.30 - the system actions, proved through the host's door

* **Contract:** unchanged (`prisma.proto` sha256 `3f8ac0fc80f46252af5a36829b142201d6054af82f0ac0254993f2bd7e4268c8`).
* **No behaviour change for a client** on a box running one release
  throughout: 0.149.29's shapes were already the door's. What changes is the
  proof - the lab now sends Reboot, Shutdown, RestartDataplane and Upgrade
  through the real doors daemon.
* **During an upgrade** (#564): between the host's door being upgraded and
  the management container being recreated - about a minute - the previous
  release's API may still answer, and its system actions can be refused by
  the new door (`FAILED_PRECONDITION`, "the host would not start it"),
  as measured on 10.40.200.166 going to 0.149.29. Nothing is changed by such
  a refusal; retry once GetVersion reports the new release. A client already
  shows a maintenance state for that window (UpgradeProgress.api_away_seconds).
* **Lab box:** .166 is on 0.149.29.

## 0.149.29 - RestartDataplane and Upgrade run on a freshly rebooted box

* **Contract:** unchanged (`prisma.proto` sha256 `3f8ac0fc80f46252af5a36829b142201d6054af82f0ac0254993f2bd7e4268c8`).
* **Fixed (#564):** on 0.149.28 a box rebooted since its last API history
  save had no `/run/prisma-routing/api`; `RestartDataplane` then hung until
  the deadline (`DEADLINE_EXCEEDED` after 180 s) and `Upgrade` streamed
  nothing, with nothing changed on the box. From 0.149.29 they run.
* **Fail fast:** a detached run that has written nothing 20 s after it was
  started now answers at once - `RestartDataplane` with
  `FAILED_PRECONDITION`, `REASON_APPLY_FAILED`, and `Upgrade` with a
  `FAILED` progress - naming the unit whose journal says why.
* **Client note:** before 0.149.29 a `RestartDataplane` result could be a
  previous run's; it is now always the run just started.
* **Lab box:** .166 is on 0.149.28.

## 0.149.28 - RestartDataplane works on a box

* **Contract:** unchanged (`prisma.proto` sha256 `3f8ac0fc80f46252af5a36829b142201d6054af82f0ac0254993f2bd7e4268c8`).
* **Fixed (#564):** on 0.149.27, `SystemService.RestartDataplane` (and the
  VTY's `system dataplane restart` from the management plane) was refused on
  a box with `FAILED_PRECONDITION` "the dataplane restart could not be
  started ... the host's door: not an operation the VTY is allowed on the
  host" - nothing was stopped. From 0.149.28 it runs. **Client note:** on
  0.149.25-0.149.26 the call was unsafe (it could leave the box without a
  dataplane), on 0.149.27 it is refused; only 0.149.28 and later run it.
* **Lab box:** .166 is on 0.149.14.

## 0.149.27 - a dataplane restart that always comes back, and rights that follow the account

* **Contract:** unchanged (`prisma.proto` sha256 `3f8ac0fc80f46252af5a36829b142201d6054af82f0ac0254993f2bd7e4268c8`).
* **Unsafe on 0.149.25 and 0.149.26 (#564):** `SystemService.RestartDataplane`
  could stop the containers and never start them - measured on
  10.95.200.198, dark until a reboot because its management is a dataplane
  interface. **Do not call RestartDataplane on those releases.** From 0.149.27
  the restart runs as a host unit of its own; the call returns its output
  as before. While the dataplane is down the API is away too; it comes back
  with the containers (its socket is reset after the restart).
* **Rights follow the account (#565):** a role or `operator` given or taken
  reaches the account's API calls at once - WhoAmI's `can_operate` and the
  calls it gates included - with no API restart. A candidate opened before
  such a change is closed and answers `NOT_FOUND` / `CANDIDATE_EXPIRED`;
  open a new one.
* **Lab box:** .166 is on 0.149.14.

## 0.149.26 - an operator may upgrade and reboot without root (#563)

* **Contract:** additive. `prisma.proto` sha256 `3f8ac0fc80f46252af5a36829b142201d6054af82f0ac0254993f2bd7e4268c8`.
* **`WhoAmIResponse.can_operate`** (5): the account may run SystemService's
  Upgrade, Reboot, Shutdown and RestartDataplane - root, or a member of
  `prisma-operator`. Independent of `role` and of `can_configure`.
* **`UserConfig.operator`** (5) in GetConfig's accounts: the account has
  `operator` in its user block.
* **How prisma-web's account gets it:** the box's administrator (root) adds
  `operator` to the account's user block and commits:
  `user prisma-web` / `role administrator` / `operator` / `exit`. From then
  on SystemService no longer answers that account `PERMISSION_DENIED`.
  An account without it still gets `PERMISSION_DENIED` with
  `REASON_PERMISSION`, and the message names `prisma-operator`.
* **The Upgrade CHECK** (`UpgradeRequest` with no version) is now every
  account's and works: it is read by the host, because the management plane
  cannot reach the releases. **Client workaround before 0.149.26:** none; on
  0.149.25 the check failed with "the newest release could not be read" for
  every account, root included.
* **Lab box:** .166 is on 0.149.14.

## 0.149.25 - nothing out of the API's reach: the clears, the log live, the box (#558, phase 5)

* **Contract:** additive. `prisma.proto` sha256 `7c924ccf4d961baaa7cea9f4d9fcf2ccea5b6773fff30a93ce8e698bde8db60f`.
* **`OperationalService.Clear`** (`ClearRequest` 1 `target`, 2 `name`, 3
  `bgp`; `ClearResponse` 1 `output`, 2 `command`): the VTY's `clear` lines.
  Targets `TARGET_INTERFACE_COUNTERS` (`name` one interface, empty every
  one), `TARGET_CGNAT_SUBSCRIBER` (an inside address), `TARGET_BGP` (a
  neighbour; `bgp` hard, soft, soft in, soft out), `TARGET_OSPF_PROCESS`,
  `TARGET_OSPF_NEIGHBOR`, `TARGET_ISIS_ADJACENCY` (a system id, or every
  adjacency). A name that is not one word is `INVALID_ARGUMENT` with
  `REASON_USAGE`; the role is the VTY's (`REASON_PERMISSION`); what the
  dataplane or FRR refuses is `FAILED_PRECONDITION` with its text. A
  subscriber clear ends sessions only: the block follows after the box's
  hold-down, as an expiry's would. Clearing interface counters restarts every
  count the telemetry and the history read for them.
* **`TelemetryService.WatchLogging`** (`WatchLoggingRequest` 1-8: `subscriber`,
  `public_address`, `port`, `event`, `pool`, `seconds`, `raw`,
  `no_deliveries`) streams `LogEvent` (1 `kind`, 2 `at`, 3 `subscriber`, 4
  `public_address`, 5 `port_first`, 6 `port_last`, 7 `pool`, 8 `pool_id`, 9
  `identity`, 10 `stream`, 11 `collector`, 12 `carrier`, 13 `connection`, 14
  `sent`, 15 `lost`, 16 `pending_bytes`, 17 `boot_time`, 18 `exported`, 19
  `from_watch_ring`, 20 `pools_named`, 21 `text`). Kinds: `STARTED` first,
  `BOOT`, `ALLOC`, `RELEASE`, `EXHAUSTED`, `DELIVERY` (every 10 s per stream
  that moved, and per box-wide loss that grew, its `stream` naming
  exhaustion_repeats / queue_dropped / export_dropped / transmit_failed),
  `LOST` (events this reader fell behind on; `lost` is how many). `at` is the
  time the exported record carries. `pool` is the configured name, `#<id>`
  when the dataplane did not say. A filter the VTY monitor would refuse is
  `INVALID_ARGUMENT`. `seconds` 0 runs until the client cancels.
* **`SystemService`** (new): `Reboot`, `Shutdown` and `RestartDataplane`
  take `SystemActionRequest` (1 `force`) and answer `SystemActionResponse` (1
  `output`); `Upgrade` takes `UpgradeRequest` (1 `version`) and streams
  `UpgradeProgress` (1 `phase`, 2 `message`, 3 `version`, 4 `newest`, 5
  `api_away_seconds`, 6 `started_at`, 7 `output`, 8 `diagnostics`);
  `GetUpgradeStatus` answers the last run's `UpgradeProgress`.
  - Reboot/Shutdown: `FAILED_PRECONDITION` with `REASON_UNSAVED` (new, 15)
    while the running configuration differs from what the next boot loads,
    `REASON_CONFIRM_PENDING` inside a confirm window - unless `force`. The
    reply comes before the box goes down.
  - A second upgrade or dataplane restart while one runs: `REASON_BUSY`
    (new, 16).
  - **The maintenance flow:** `Upgrade` streams `STARTED`, `DOWNLOAD`,
    `VERIFY`, `INSTALL` per row, `INSTALLED`, then `API_RESTART` with
    `api_away_seconds` (60) - and the stream ENDS there, before the API
    restarts. Show the box as "in maintenance" from `STARTED`, keep it so
    for `api_away_seconds` after `API_RESTART`, then reconnect and read
    `GetVersion`; `GetUpgradeStatus` reads the run back from the host if the
    client lost the stream. A `FAILED` carries the run's output and its
    reason. An empty `version` installs nothing: one `CHECKED` with `newest`.
  - **Who may:** the VTY's rule - the host's doors for these are root's, so
    an account that is not root (prisma-web's certificate is `prisma-web`)
    gets `PERMISSION_DENIED`, `REASON_PERMISSION`, as it would at the prompt.
    Granting it is a decision for the maintainer (#212 recorded why it is
    not today).
* **Health:** `prisma.v1.SystemService` answers `SERVING`.
* **Coverage:** [api-coverage.md](api-coverage.md) lists no path as `none`
  (630 typed, 19 text: the `show` screens without an rpc and the probes).
* **Lab box:** .166 is on 0.149.14.

## 0.149.24 - the rest of the configuration as fields (#558, phase 4)

prisma-web's ask (c) on #558: system, users/SNMP and policies typed, and
with it every other configuration area - after this release no
configuration statement is reachable only as text.

* **Contract:** additive. `prisma.proto` sha256 `4403c9c2813624c4dc9135821388512de1c74be78450315cff1256ce1f0fe883`.
* **GetConfig's new areas** (TypedConfig 14-25): `system` (`SystemConfig`:
  hostname, dns_servers, ssh, api, snmp, dataplane_interface,
  cgnat_memory_gb, cpu and memory as `KeyValue` pairs), `users`
  (`UserConfig`), `snmp_users` (`SnmpUserConfig`), `policy`
  (`PolicyConfig`: named lists, statements, terms - `PolicyTermConfig.from`
  is a list of `PolicyMatchConfig` by kind, `sets` of `PolicySetConfig`,
  `then`; `unread` for a line the parser could not read), `route_maps`,
  `frr_lists`, `rpki`, `routing_attachments`, `vrfs`, `vrrp`, `bundles`,
  `cgnat_globals`. No area named is still every area, these included.
* **Settings:** `BgpConfig`, `BgpFamilyConfig`, `BgpNeighborConfig`,
  `BgpNeighborFamilyConfig`, `OspfConfig` and `IsisConfig` gain `settings`
  (`KeyValue`): the knobs phase 3 kept in `other` - `bgp
  log-neighbor-changes`, `maximum-prefix`, `auto-cost reference-bandwidth`,
  `set-overload-bit`... - key the statement's keywords, value the rest (`""`
  a flag). **Moved:** such a statement is no longer in `other`; read both
  if you showed `other`.
* **Sentinels and secrets:** 0 / `""` is "not stated" throughout (a port 0,
  an identity source `""`); `role` `""` is a read-only account. **No secret
  is ever read:** `password_set`, `ssh_keys` (a count), `snmp.communities`
  (a count), a trap host without its community, an SNMP user's protocols
  only, an IS-IS password as `"<set>"`, `identity_radius_secret_set`.
* **Edits:** `SetConfigRequest.system` (8) and `.vrf_device` (9);
  `DeleteConfigRequest.area` `"vrfs"`. System fields are named by block and
  key (`ssh.port`, `api.listen_address`, `snmp.enterprise_number`) - **name
  them**: an empty `fields` applies every field, and an unset `dns_servers`
  removes the resolvers. A secret is never a typed field: naming one is
  `INVALID_ARGUMENT`; set it as a line.
* **GetSchema:** `SchemaStatement.read_typed` (8) - GetConfig reads it as a
  field - beside `typed` (SetConfig writes it too). `SchemaMode.typed_area`
  now names the area of the routing, system, account and policy modes.
* **GetSchema fixes (your three reports on 0.149.22):**
  - `words` are literal keywords only. A placeholder is an argument with
    `position` (8): how many words come before it. router static's statement
    is words `["next-hop"]` with `<prefix>` at 0; `neighbor <address>
    remote-as` is words `["neighbor", "remote-as"]` with `<address>` at 1.
  - A `|` between a placeholder and words is one FORM per kind: `remote-as`
    has a form with an integer 1-4294967295 and one with the choice
    `internal`/`external`. No choice value holds `<` or `>`, none is empty;
    a compound pattern (`rt|soo:<asn>:<value>...`) is one `word` argument.
  - Order: a keyword-led part has a `group` (10) and is `unordered` (9) -
    match such parts in any order after the words; the other arguments keep
    theirs (`[seq <n>] permit|deny <prefix>`: `seq <n>` a part, the choice
    and prefix positional). `many` (11): one or more values to the end.
  - Lines that matched nothing now match: the hints of `snmp trap-host`
    (`<address> community <string> [inform]`), `ssh-key` (`[<comment>...]`,
    not the keyword `comment`), `vrrp` (the VRID in every form), a
    neighbour's `route-map`/`prefix-list` (`<name> in|out`), an OSPF `area`
    (`stub`, `nssa`, `range`, `authentication`, `default-cost`) and the FRR
    lists at the configuration level give their whole syntax, and `vrf
    <name> table <id>` and `route-map <name> permit|deny <seq>` are
    described, and so is an operator VRF's FRR block (`vrf <name>`, its
    `ip route`/`ipv6 route`: mode `vrf`, 27 modes). 412 statements (from 407). Every line of the lab box's
    GetRunning matches one; `typedcfg.unmatched_lines` is the matcher.
* **docs/api-coverage.md:** 611 command paths typed (from 278), 0
  configuration statements text-only; what stays text is operational. Its
  format is unchanged; some rows read longer because those hints now give
  the whole syntax (`ip prefix-list <prefix-list> [seq <1-4294967295>]
  permit|deny ...`), and IPv6 rows already said `<X:X::X:X/M>` since
  0.149.19.

## 0.149.23 - `no neighbor X` removes a BGP neighbour cleanly (#562)

* **Contract:** unchanged (`prisma.proto` sha256 `11163ea9328b3a12c8cf90d28af4e75ce9972b3c45a9f06842de62544b8a4d01`).
* **Behaviour:** an Edit carrying `no neighbor X` at a `router bgp` path now
  removes the neighbour and every statement naming it, as DeleteConfig has
  since 0.149.22, and commits clean. Before, that commit reported statements
  FRR "did not keep" or rolled back on frr-reload. A peer-group that a member
  still names is refused (`GRAMMAR`, nothing staged), its message naming the
  members. A `no <statement>` removes the statement in the block of the path
  it is sent at - the same neighbour in the default router and a VRF's are two.
* **Lab box:** .166 is on 0.149.14.

## 0.149.22 - the routing configuration as fields (#558, phase 3)

prisma-web's ask (b) on #558: the BGP and OSPF forms stop parsing running text.

* **Contract:** additive. `prisma.proto` sha256 `11163ea9328b3a12c8cf90d28af4e75ce9972b3c45a9f06842de62544b8a4d01`.
* **GetConfig's routing areas:** `static`, `bgp`, `ospf`, `ospf6`, `isis`,
  `interface_routing` - new `TypedConfig` fields 8-13 (`static_routes`,
  `bgp`, `ospf`, `ospf6`, `isis`, `interface_routing`) with
  `StaticRouteConfig`, `BgpConfig`, `BgpNeighborConfig`, `BgpFamilyConfig`,
  `BgpAggregateConfig`, `BgpNeighborFamilyConfig`, `RedistributeConfig`,
  `OspfConfig`, `OspfNetworkConfig`, `OspfAreaConfig`, `IsisConfig` and
  `InterfaceRoutingConfig`. **No area named is now every area**, the routing
  ones included (in 0.149.20 it was the three of phase 2); name the areas you
  want to keep a read small.
* **Where it comes from:** the FRR statements the configuration holds - the
  routing half IS FRR's text on this box - read block by block through the
  VTY's own block reader, so a field is in the block `show running-config`
  shows it in. Every block keeps what no field takes in `other`, verbatim:
  show it read only; nothing is dropped.
* **Sentinels:** `distance` 0 / `tag` 0 not stated; `remote_as` "" when a
  peer-group gives it, else a number or `internal`/`external`; `asn` is a
  string (asdot is legal); `keepalive`/`holdtime` 0/0 the defaults;
  `ebgp_multihop` 0 not stated; `area_type` "normal" for an area with no type
  statement; `is_type` "" not stated; `default_information` "" absent. A
  route-map named `PO-...` is a routing policy's compiled map. **A BGP
  password is never read**: `password_set` only.
* **Typed edits:** `SetConfigRequest.static_route` (6) and `.bgp_neighbor`
  (7, `BgpNeighborEdit`: asn, vrf, neighbor, write-only `password`,
  `families` of `BgpNeighborFamilyEdit`). A static route is retyped whole
  (description included), the fields not named kept from the candidate; a
  VRF's route is refused with `REASON_USAGE` (edit it as a line in its `vrf`
  block). A BGP neighbour edit writes only what differs; **a family listed is
  set whole** (a route-map it does not name is removed); `fields` for a
  neighbour are `remote_as`, `description`, `update_source`, `timers`
  (keepalive + holdtime), `password`, `peer_group`, `ebgp_multihop`,
  `shutdown`, `bfd`, `families`; a `password` not given is never cleared.
  `DeleteConfigRequest` gains `prefix` (5), `next_hop` (6), `asn` (7), `vrf`
  (8): `area` "static" with prefix and next_hop, or "bgp" with `name` (the
  neighbour) and asn/vrf. A neighbour is removed **statement by statement**
  (each family's, then the router level's, `remote-as` last): a bare `no
  neighbor X` is staged by this VTY as a statement of its own, and with a
  family statement frr-reload refuses it. **Replies never echo a BGP
  password**: `lines` and `output` show `password <set, not shown>`, as
  `show running-config` does.
* **`BgpConfig.default_ipv4_unicast` (7):** FRR's default is on, and then a
  neighbour is active in ipv4 unicast with NO `activate` line (FRR does not
  print one). A family's `activate` is what is stated; read it with this. OSPF, OSPF6 and IS-IS are written as lines through
  Edit (GetSchema describes them).
* **Coverage:** docs/api-coverage.md now classes 278 command paths typed (169
  in 0.149.20); its format is unchanged.

## 0.149.21 - `no mtu` goes back to the default, and read-only reads are fast

* **Contract:** unchanged (`prisma.proto` sha256 `4cdfee65edf97ae1239d5c4a18648250b04a0d08ff4d4b6106b117e47ea86c09`).
* **Fixed (#561):** an account with no configuration role waited about 90 s
  for any read of the running configuration - `GetRunning`, `GetConfig` and
  `Show running-config` alike. It is as fast as an administrator's now: 0.42 s
  against 0.39 s in the lab, from 90.6 s, with the same content. **Client
  workaround before 0.149.21:** a deadline of at least 120 s on those calls
  for such an account.
* **Fixed (#560):** removing an interface's MTU (`SetConfig` clearing `mtu`,
  or the line `no mtu`) now sets the interface back to the box's default MTU;
  before, the dataplane kept the old value and `GetConfig` went on reporting
  it. The running configuration still names an MTU for every interface - the
  device's, which after the removal is the default (9000 unless the tuner set
  `ethernet { default-mtu }`). A client that wants "no explicit MTU" compares
  against that default; it is not a separate field.
* **Lab box:** .166 is on 0.149.14.

## 0.149.20 - the configuration as fields (#558, phase 2)

prisma-web's items 4 and 5: interfaces, CGNAT pools and the CGNAT log export
read and written as fields.

* **Contract:** additive. `prisma.proto` sha256 `4cdfee65edf97ae1239d5c4a18648250b04a0d08ff4d4b6106b117e47ea86c09`.
* **New rpcs (ConfigService):** `GetConfig`, `SetConfig`, `DeleteConfig`,
  `GetSchema`, `ValidateLines`, with `InterfaceConfig`, `PoolConfig`,
  `PortForwardConfig`, `LogCollectorConfig`, `LoggingConfig`, `TypedConfig`
  and the schema messages. Field numbers and meanings are in the proto and in
  docs/api.md, "The configuration as fields".
* **Defaults and sentinels:** a field at its default is a statement the block
  does not have - `mtu` 0, `vrf` 0, `cgnat_role` "", `linux_name` "" (the
  interface's own name), `port_first`/`port_last` 0/0 (the device's range),
  `max_blocks` 1, `tcp_mss` "" (off); a pool's `vrf`/`outside_vrf` UNSET is
  every VRF, which is not VRF 0 (check `HasField`); a syslog collector's
  `facility`/`severity` unset are the box's defaults. `LoggingConfig` always
  lists the three collectors, udp, tcp, syslog, in that order, on a read.
* **Replacing front/src/lib/vty.ts's line building:** send the object to
  `SetConfig` instead of building lines - the server writes them from the
  candidate's current state, stages them as `Edit` does, and answers them
  in `SetConfigResponse.lines` (show them as the preview). Name the
  `fields` you mean: an empty mask sets EVERY field to the value sent, so a
  partial object with an empty mask clears what it leaves out. Refusals
  carry the 0.149.18 reason codes; a request whose fields cannot become a
  line is `INVALID_ARGUMENT` / `REASON_USAGE`.
* **Every configuration area editable from the web, as lines checked
  against the grammar:** `GetSchema` is the whole configuration grammar,
  generated from the VTY's own tree - 26 modes (CGNAT, interfaces, system,
  users, SNMP users, policies, router static/bgp/ospf/ospf6/isis, rpki,
  route-maps) with about 400 statements: the `path` to type each mode's
  statements at, each statement's forms, each argument's kind (`integer`
  with its range, `ipv4_prefix`/`ipv6_prefix`/`prefix`, `ipv4_address`/
  `address`, `choice` with its values, `text`), `optional`, `repeatable`,
  the `no` words, the `source` the VTY completes it from (`interfaces`,
  `neighbours`, `next_hops`, `prefix_lists`, ...), and `typed` where
  GetConfig/SetConfig carry it as a field. Cache it by `digest` (it changes
  only with the grammar; `release` says whose); `with_values` adds each
  source's values now. `ValidateLines` then asks the VTY itself, line by
  line, in a candidate nobody sees, with the reason of each refusal; `Edit`
  stages them. Typed reads of the routing configuration (static, BGP
  neighbours and address families, OSPF, IS-IS) are the next phase.
* **The box's own lines, read only on purpose:** `TypedConfig.box_owned`
  lists the running configuration's lines that are the box's, not the
  operator's - `log syslog <level>`, `service integrated-vtysh-config`, a
  `vrf prisma-*` block and its `exit-vrf` (on .166, 4 of its 30 lines) -
  each with why; `ConfigSchema.box_owned` lists their patterns. No grammar
  takes them and the VTY marks them "not checked": show them read only, not
  as unknown. An operator's own VRF block is not among them.
* **IPv6 placeholders:** docs/api-coverage.md printed `ipv6 address
  <A.B.C.D/M>` (and its `no`) through 0.149.18. The VTY's own hint was
  wrong; 0.149.19 (#557) corrected it to `<X:X::X:X/M>` and the document,
  generated from that hint, followed. A sweep of every statement finds no
  other IPv6 argument labelled IPv4 (an OSPFv3 `router-id` is a dotted quad
  by the protocol). This is a correction within the same syntax, not a
  change of the format the next item keeps.
* **docs/api-coverage.md stays a contract** until you have moved to
  GetSchema: its statement syntax (`<A.B.C.D/M>`, `<1-N>`, `a|b`,
  `[optional]`) and its "Entered with ... `EditRequest.path` ... changed by"
  lines keep their shape; a change to either is announced here first.
* **Replacing the 0.149.8 guard's diff parsing:** `GetConfig(areas:
  ["logging"])` says which collectors are configured, typed. The guard itself
  is not needed on a box at 0.149.8 or later: disabling a syslog that was
  never enabled is harmless there (#551). For boxes before 0.149.8, keep it,
  reading `configured` from GetConfig instead of the diff.
* **What is not typed yet:** an interface's VRRP, IS-IS and OSPF statements,
  a bundle's members and LACP, and the rest of the configuration stay lines
  through `Edit`; docs/api-coverage.md lists each (169 commands typed now).
* **Lab box:** .166 is on 0.149.14.

## 0.149.19 - TAB completion that helps remove, finish and address (#557)

* **Contract:** unchanged since 0.149.18 (`prisma.proto` sha256
  `8fdfa497...50ed5d`).
* **Behaviour:** the VTY only. Completion after `no` offers what is
  configured; optional words after a positional argument complete; `?` on a
  complete statement prints an `<Enter>` line; next hops and listen addresses
  complete from the box. A client that runs `?`-style help through `Show` has
  none - `?` is a terminal key, not a command - so nothing an API client reads
  changes. The `logging syslog` hint now spells its options, which a client
  that shows the VTY's own help text would see.

## 0.149.18 - why a change was refused, the confirm window, the candidates (#558, phase 1)

Phase 1 of #558, ordered by prisma-web's own list (items 1-3).

* **Contract:** additive. `prisma.proto` sha256 `8fdfa4978201d4fa54fe5d0b8b368bc3ae95d6ca163d23c2bb0ac303c550ed5d`.
* **Refusal reasons:** every refused ConfigService call carries
  `Diagnostics.reason` in the `prisma-diagnostics-bin` trailer:
  - `STALE_BASE` (`FAILED_PRECONDITION`) with `running_revision`, the revision
    GetRunning answers now;
  - `PERMISSION` (`PERMISSION_DENIED`);
  - `CANDIDATE_EXPIRED` and `CANDIDATE_UNKNOWN` (both `NOT_FOUND`);
  - `LIMIT` (`INVALID_ARGUMENT` at entry, `FAILED_PRECONDITION` at a commit)
    with `limit_field`, `limit` and `limit_size`,
    all in bytes (e.g. `cgnat identity file`, 255, 256);
  - `GATE_REMAP`, `GATE_DIVERGE`, `GATE_CUTOFF` with `gate_items`;
  - `CONFIRM_PENDING`, `GRAMMAR`, `VALIDATION`, `APPLY_FAILED`, `USAGE` and
    `OTHER`.

  `CheckResponse.reason` (7) is the code a Commit would meet, so a gate is
  known before committing. The human message is unchanged.
* **Replacing the stale workaround:** `routes/config.rs is_stale` compares
  the candidate's base with GetRunning. Read `REASON_STALE_BASE` and
  `running_revision` instead; the comparison is no longer needed.
* **GetPendingConfirm** (ConfigService): `pending`, `revision` (the running
  revision the box would revert from), `deadline`, `account`, `comment`,
  `committed_at`, `minutes`, `revert_attempts` and `overdue`. It is read from
  the box's own state: the same after an API server or a back-end restart,
  and for any account. A read-only account gets `minutes` 0 because it
  cannot read the window file, but `deadline` is still right. Replace the
  in-memory banner with this call at start and on every
  `confirm-pending`/`confirmed` event.
* **Candidate lifecycle:**
  - `ListCandidates` gives the calling account's candidates, oldest first,
    each with `base_revision`, `created_at`, `last_activity`, `expires_at`
    and `staged_lines`, plus the server's `idle_seconds` (1800 on a box).
    Listing does not count as activity.
  - `OpenCandidateResponse` gains `expires_at` (4) and `idle_seconds` (5).
  - `EditResponse` gains `last_activity` (3) and `expires_at` (4).

  Replace the 25-minute guess and the "NOT_FOUND means expired" mapping with
  `expires_at` and `REASON_CANDIDATE_EXPIRED`.
* **Lab box:** .166 is on 0.149.14.

## 0.149.17 - what the API reaches, command by command

* **Contract:** unchanged (`prisma.proto` sha256 `b08a5a08...0c3e7a`).
* **New:** [docs/api-coverage.md](api-coverage.md), generated from the VTY's
  own grammar: every command (672 paths) classified as typed, text (the
  screen through `Show`, or a configuration line through `Edit`), none, or
  not applicable, with the rpc that serves it. A test keeps it current. The
  17 paths with no API yet (`monitor cgnat logging`, `clear bgp/ospf/isis`,
  `system reboot/shutdown/upgrade/dataplane restart`) and the phases that
  will type the rest, in prisma-web's priority order, are listed there (#558).
* **Lab box:** .166 is on 0.149.14.

## 0.149.16 - monitor cgnat logging

* **Contract:** unchanged (`prisma.proto` sha256 `b08a5a08...0c3e7a`).
* **New, VTY only (#556):** `monitor cgnat logging` prints the block events
  the box exports, live. It is not reachable through the API: `Show` takes
  `show`, `ping`, `traceroute` and `mtr` only, and refuses `monitor` as it
  always has - GetLogging (0.149.15) remains the API's view of the export,
  and a client that wants the records themselves reads them at its collector.
* **New dataplane command:** `show bpa pool index` (`<index> <name>` per
  pool), which maps the `natPoolId` / `pool=` a record carries to the pool's
  name; the monitor uses it. Not an API addition.
* **Segment:** the shared segment gains a watch ring after the export ring;
  its layout version stays 3.
* **Lab box:** .166 is on 0.149.14.

## 0.149.15 - the CGNAT log export in the API

* **Contract:** additions only. `prisma.proto` sha256 `b08a5a089ff29ecc6c4d44f4cad3f51864d225d3ed210726953eee7a480c3e7a`
  (was `269fb5c9...106f`).
* **New rpc `OperationalService.GetLogging`** -> `LoggingState`:
  * `streams` - always three, in this order: `syslog`, `ipfix-tcp`,
    `ipfix-udp`. Each a `LogStream`: `configured`; `collector`
    ("address:port"); `source` (UDP only); `carrier` ("log-container" or
    "dataplane", empty when not configured) and `carrier_reason`;
    `connection` (TCP: "connected" / "connecting" / "disconnected"; empty for
    UDP); counters `records`, `bytes_sent` (TCP), `packets_sent` (UDP from
    the log container; 0 when the dataplane sends), `dropped_over_cap`,
    `send_failures`, `dropped`; gauges `pending_bytes`, `spill_bytes`, and
    `spill_cap_bytes` (256 MiB); the verdict `delivering`, `stalled_reason`
    and `last_progress`.
  * box-wide: `carrier` and `logger_pid` (who carries the TCP streams),
    counters `export_ring_dropped`, `worker_queue_dropped`,
    `exhaustion_repeats_not_reported` (expected under exhaustion, not a
    loss), `transmit_failed` (every sender on that interface) with
    `transmit_interface`; `rings_shared`; and `text`, the `show cgnat
    logging` screen as read.
  * Counters count from the dataplane's start and are the CURRENT carrier's:
    a stream moving between the container and the dataplane can make one
    jump or restart - take a decrease as a reset. Not configured: every
    field empty or 0.
* **Health:** `Check(service="prisma.v1.LogExport")` is `NOT_SERVING` while
  any configured stream has had something to send and got nothing out for
  60 s, or the dataplane does not answer; `SERVING` otherwise (also with
  nothing configured). `""` and `OperationalService` are not affected by it.
* **Events:** `SubscribeRequest.logging_events = 6` adds `Event`s of kinds
  `log-connected`, `log-disconnected`, `log-carrier`, `log-spill`,
  `log-spill-full`, `log-stalled`, `log-delivering`, `text` naming the
  stream. Up to 10 s late (the server reads every 10 s).
* **Lab box:** .166 is on 0.149.14 (upgraded and rebooted on 2026-10-02; it has
  no log export configured, so its GetLogging shows three unconfigured streams).

## 0.149.14 - UDP IPFIX sent by the log container too

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Behaviour:** the RFC 8158 export over UDP is sent by the log container
  when it runs, from the same source address and port, with the same
  templates and fields; nothing an API client calls changes. What
  `OperationalService.Show` returns for `show cgnat logging` gains one line
  under the IPFIX section: `sender      the log container: N records, N
  packets, N templates, N send failures, N dropped`, or `sender      the
  dataplane`, with a reason after a colon when the container is up and does
  not send it. A client parsing that text should not assume the old line
  count.
* **Not yet:** the logging counters are still not in the API (they are only
  in `show cgnat logging`).

## 0.149.13 - the SNMP MIB under the product's own number, 67033

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **SNMP, for a client that also polls it:** with no `snmp enterprise-number`
  configured, PRISMA-ROUTING-MIB and its notifications are rooted at
  `.1.3.6.1.4.1.67033` (the product's IANA Private Enterprise Number) instead
  of `.1.3.6.1.4.1.32473`. Object numbers below the root are unchanged; only
  the prefix moves. A box that named its own number keeps it.
* **Lab box:** .166 is on 0.149.12 since 2026-10-02 (upgraded from 0.149.4,
  a reboot; the API was away a few minutes).

## 0.149.12 - the TCP log streams in a log container of their own

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Behaviour:** syslog over TCP and IPFIX over TCP are carried by a new
  `logger` container (prisma-logd) instead of the dataplane, so replaying a
  large spill or a stalled collector no longer costs forwarding (measured: 0
  packets lost in the lab, against 60-72% with the old path). Nothing an API
  client calls changes. What `OperationalService.Show` returns for `show cgnat
  logging` gains two lines - `TCP streams carried by the log container (pid
  N)` or `... by the dataplane: no log container is running`, and `export
  N event(s) dropped with the log container's ring full (65536)` - and its
  stream counters are the container's while it carries the streams. A client
  parsing that text should not assume the old line count.
* **Health:** unchanged. The log container is not a gRPC service and its
  state is not in Health yet.
* **Lab box:** the log container arrives with this patch: an upgrade brings
  it up with the VTY's container (about a minute without the API, as every
  upgrade).

## 0.149.11 - the log rings in a segment of their own (#550, step 1)

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Behaviour:** none an API client sees. Each worker's queue of CGNAT block
  events moves out of the dataplane's heap into a shared-memory segment of
  its own (`/dev/shm/prisma-bpa-log`); the dataplane still sends the log
  itself. `show cgnat logging` gains a `rings` line (where the segment is, or
  why the box fell back to the heap). The counters stay on the VTY only.
* **Lab box:** .166 is on 0.149.4.

## 0.149.10 - an identity path is refused at 256, where it always failed

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Behaviour (#553):** a candidate line `cgnat identity file <path>` or
  `cgnat identity dhcp-leases <path>` with a path of 256 characters or more is
  refused by the commit before anything is sent, naming the limit of 255 (a
  `FAILED_PRECONDITION` with that text over the API). Before, the commit of a
  256-character path failed later with "cannot be sent over the binary API".
  A client that bounds the field in a form should use 255.
* **Lab box:** .166 is on 0.149.4.

## 0.149.9 - a clean exit nobody asked for is started again

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Behaviour (#552):** a dataplane that exits with status 0 while nothing on
  the box stopped it is started again and its configuration put back, like a
  crash since 0.149.5 - within about 10 s plus the time the dataplane takes to
  start. Before, the box stayed without a dataplane until a reboot. An API
  client sees the same window as for a crash: Health `NOT_SERVING` for
  `OperationalService` and `TelemetryService` while the statistics segment
  does not answer, and the dataplane's counters starting again from zero.
  Five such restarts a boot; after that the box gives the card back and
  stays down, as before.
* **Lab box:** .166 is on 0.149.4.

## 0.149.8 - disabling a syslog never enabled no longer stops the dataplane

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Fixed (#551):** on 0.149.7 and before, `logging syslog disable` on a box
  whose syslog had never been enabled closed the dataplane's stdin, and the
  next operation stopped the dataplane (clean exit, not restarted). An API
  client saw it as Health `NOT_SERVING` for `OperationalService` and
  `TelemetryService`, `UNAVAILABLE` on reads that need the dataplane, and no
  recovery until the box was rebooted. **Client workaround before 0.149.8:**
  none on the client side; a commit that disables syslog should not be sent
  to a box whose syslog was never enabled.
* **Lab box:** .166 is on 0.149.4.

## 0.149.7 - CGNAT log export stops endangering the dataplane

Fixes #547 and #549.

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`). No field,
  rpc or event was added or changed.
* **Behaviour:** a stalled or absent syslog collector no longer grows the
  dataplane's memory (a 200 MB spill replays with the main heap moving 2 MB)
  and no longer delays workers. A slow collector keeps its connection. The
  boot marker goes into a TCP log stream once per boot. Exhaustion
  (natEvent 12) is reported once per subscriber per 10 s per worker, not
  once per refused packet. Nothing a client reads over the API changes.
* **New counters: `show cgnat logging` only.** None is in `SystemInfo`, the
  telemetry stream or any rpc (that waits for #550, announced above). All
  are cumulative since the dataplane started (a dataplane restart zeroes
  them), except the spill file's size, which is a gauge read when asked.
  - `exhaustion` - repeats not reported: one figure for the box, all
    workers summed; it is IPFIX and syslog together (the event is held back
    before either transport sees it).
  - `queue` - events dropped because a worker's queue was full: one figure
    for all workers, before either transport (not split by transport).
  - `transmit` - packets the egress interface toward the IPFIX collector
    failed to send, since its counters were last cleared, **from every
    sender on that interface**, not only the log.
  - `file`, once per TCP stream (the IPFIX-over-TCP stream and the syslog
    stream, **counted apart**): bytes in the spill now (gauge), the 256 MB
    ceiling, bytes dropped over it, bytes skipped to find a message, stalls.
    Bytes dropped over the ceiling are also added to that stream's existing
    `dropped` figure, which the statistics segment already published.
* **Lab box:** .166 is upgraded after the release; announced to the
  prisma-web session before it runs.

## 0.149.6 - this file

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **New:** docs/clients.md, the per-release notes for API clients, with the
  releases since 0.149.2 written back into it.
* **Lab box:** .166 is on 0.149.4.

## 0.149.5 - a dataplane that restarts after a crash comes back configured

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Behaviour:** if the dataplane container restarts after a crash, the box
  puts the running configuration back by itself within about 10 seconds
  (10.8 s measured). Before, it stayed with its interfaces down and no pool
  until someone re-applied. The API runs in the management container and does
  not restart with the dataplane; during those seconds `GetSystem` and the
  telemetry read the restarted dataplane (its counters start again from
  zero), and Health
  answers `NOT_SERVING` for `OperationalService` and `TelemetryService` while
  the dataplane's statistics segment does not answer.
* **Lab box:** .166 is on 0.149.4; it moves to 0.149.5 or later with the next
  announced upgrade.

## 0.149.4 - the API comes back after an upgrade recreates the VTY's container

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Fixed (#546):** after an upgrade the API could stay unreachable for about
  ten minutes: the backplane kept a stale route to the old container. Since
  0.149.4 the API answers again as soon as the new container is up.
  **Client workaround before 0.149.4:** a connection refused right after an
  upgrade can persist; retry with backoff for up to 15 minutes.

## 0.149.3 - an unknown link speed is 0, not 4.3 Tbps

* **Contract:** unchanged (`prisma.proto` sha256 `269fb5c9...106f`).
* **Fixed (#545):** `Interface.speed_bps` was 4294967295000 for an interface
  whose speed the dataplane does not know (VPP's 0xFFFFFFFF kbps, seen on
  virtio). It is 0 now, as the contract says. **Client workaround on 0.149.2:**
  treat 4294967295000 as unknown, like 0.

## 0.149.2 - routes, history, worker load and who a client is (#533)

* **Contract:** `prisma.proto` sha256 `269fb5c94a754c7c44719c6983a58860e69fbbc9bcff8abce58f26aaace3106f`,
  plus `api/health/v1/health.proto` (gRPC's own `grpc.health.v1`). All
  additive: no field renumbered.
* **`SystemInfo`** 17-20: `memory_total_bytes`, `memory_used_bytes` (MemTotal
  less MemAvailable), `memory_hugepages_bytes` - gauges, bytes - and
  `repeated WorkerLoad worker_load`.
* **`WorkerLoad`**: `cpu`, `vectors_per_call`, `busy_ratio`,
  `loops_per_second`, `busy_window_ms`, sampled every 10 s. `busy_ratio` is
  the share of node clocks over the window: **a saturated worker reads about
  0.5, not 1.0** (receive work inside the input node is not counted). Scale a
  load display to that.
* **`Interface.speed_bps`** (10): bits per second, 0 when the dataplane
  reports none (a link that is down, a virtual one) - see 0.149.3.
* **`Event`** 4-5: `revision` and `comment`. The revision is read as the
  subscriber's own account, so it equals that account's
  `CommitResponse.revision` and `GetRunning` revision; it is empty when another
  commit landed during the read. A read-only account sees a different id for
  the same configuration.
* **`TelemetryService.GetHistory`**: a stream of `HistorySample`, one a
  minute, the last 24 hours (interfaces capped at 256). Kept in the host's
  `/run`: it survives upgrades and the container being recreated, **not a
  reboot**.
* **`RouteService.ListRoutes`**: paged from one snapshot; filters vrf,
  family, prefix (and what is inside it), protocol, selected. A snapshot
  nobody pages for 10 minutes is dropped (its token then answers
  `FAILED_PRECONDITION`: start again); four snapshots per routes process at
  most; a page waits up to 60 s for the snapshot, then `UNAVAILABLE`.
  Measured: 200,001 routes in 5000-route pages, first page 1.0 s, all 12.4 s.
  `GetRouteSummary` gives the counts.
* **`SessionService.WhoAmI`**: `account` (the certificate's CN), `role`,
  `can_configure`, `can_configure_cgnat`.
* **Health:** `SERVING` for `""` and each service; `NOT_SERVING` for
  `OperationalService` and `TelemetryService` while the dataplane's
  statistics segment does not answer. Needs a CA-signed client certificate,
  not one naming a box account.
* **Fixed:** `Event.account` was always empty, and an expired `commit
  confirmed` sent no rolled-back event.
