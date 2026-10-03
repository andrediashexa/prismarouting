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
