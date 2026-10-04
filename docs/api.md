# The management API (prisma.v1)


Which VTY command the API reaches, and how (typed, text, or not yet), is
[docs/api-coverage.md](api-coverage.md), generated from the VTY's grammar.
What each release changed for an API client - fields, limits, sentinels,
known bugs and the lab box's upgrades - is in [docs/clients.md](clients.md),
newest first.

## TL;DR

A box serves a gRPC API, package `prisma.v1`, for a management client - the
web interface's Rust backend first - over **mutual TLS** on the management
network, off by default. It is **not a second configuration system**: every
configuration call is the VTY's own `configure`, the lines an operator types,
`commit check`, `commit`, `commit confirmed`, `rollback` and `save`, run by the
VTY's code as the account the client certificate names, so a refusal is the
VTY's refusal in the VTY's words and a change made through the API is in
`show running-config` at the prompt. The contract is
[`api/prisma/v1/prisma.proto`](../api/prisma/v1/prisma.proto) (#532).

> The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
> are to be interpreted as described in
> [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## 5W2H

| | |
|---|---|
| **What** | `ConfigService` (the candidate and its commit), `OperationalService` (typed reads and any `show`), `TelemetryService` (a stream of counters, CGNAT totals and configuration events, and a day of their history), `RouteService` (the routing table, typed and paged), `SessionService` (who a client is to the box), and gRPC's own `grpc.health.v1.Health`. |
| **Why** | A web interface drives the box without screen-scraping a terminal, and without becoming a second place the configuration is decided. |
| **Who** | A management client holding a client certificate the box trusts; each call acts as the box account its certificate names. |
| **Where** | The server runs in the management container; the host listens on the management address and carries the connection to it. |
| **When** | A first cut from 0.148.4; held by its lab, with what the lab and the X520 bench found, from 0.148.5. Off until an operator sets `api enabled yes` and `api listen-address`. |
| **How** | gRPC over mutual TLS; configuration as the VTY's own text; typed operational data. |
| **How much** | One port on the management address; one process per open candidate and per account that reads. |

## The rules the contract keeps

* **Configuration travels as the VTY's text.** A candidate is edited with the
  lines an operator types, at a hierarchy path (`["nat", "cgnat", "pool P"]`
  and `block-size 256`), and read back as the text
  `show running-config` prints. No configuration statement is a protobuf
  message: modelling the grammar twice is how two descriptions of one box
  drift. The **operations** and the **operational data** are typed.
* **Compatibility**: inside `prisma.v1` a field, an rpc or an enum value is
  only ever added, with a number never used before; nothing is renumbered,
  retyped or reused. Anything else is `prisma.v2`, served beside `v1`. The
  rules are at the head of the `.proto`.
* **Services are grouped by subject** (#533): a new rpc goes into the
  service its subject belongs to, and a new service comes only with a new
  subject - the routing table (`RouteService`), the client's own session
  (`SessionService`). A client that generates its server traits with stubs
  answering `UNIMPLEMENTED` by default (tonic-prost-build's
  `generate_default_stubs(true)`) keeps building when an rpc is added to a
  service it implements, as a mock; one that does not has to implement the
  new rpc before it builds.
* **Errors** are gRPC status codes, with the VTY's own account of the refusal
  in the trailing metadata key `prisma-diagnostics-bin` (a serialized
  `Diagnostics`): `INVALID_ARGUMENT` for a line the VTY refuses,
  `FAILED_PRECONDITION` for a commit it refuses or a candidate whose base has
  moved, `PERMISSION_DENIED` for an account without the role, `NOT_FOUND` for
  a candidate that does not exist, `UNAVAILABLE` when the box cannot answer.
  `Check` is not an error when it finds problems: answering that is its job.
  **Why**, as a code a client acts on, is `Diagnostics.reason` (#558, see
  [Why a change was refused](#why-a-change-was-refused)); the VTY's words
  stay the message for a person.
* **A candidate is a session**, held by a process of its own that has become
  the account (its groups, its uid) before it built the VTY's `Shell`. Its
  commit is refused, with the difference, when the running configuration has
  changed since it was opened - the client opens a new candidate and applies
  its lines again, rather than overwrite a change somebody else made.

## The calls

| Service | rpc | What the VTY does |
|---|---|---|
| ConfigService | `OpenCandidate` | `configure` (or `rollback <n>`) |
| | `Edit` | the lines, typed at the path |
| | `ShowCandidate` | the candidate's text |
| | `Compare` | `show configuration compare` |
| | `Check` | `commit check`, with the `remap` / `cutoff` / `diverge` gates |
| | `Commit` | `commit [remap] [cutoff] [diverge] [confirmed <min>] [comment <text>]` |
| | `Confirm` | a `commit` inside the confirm window |
| | `Rollback` | `rollback <n>` into the candidate |
| | `Save` | `save` |
| | `CloseCandidate`, `GetRunning`, `ListRevisions` | |
| | `GetPendingConfirm` | the `commit confirmed` window the box holds (#558): its deadline, who committed it and why, the revision it reverts from - from the box's own state, so the same after a restart of the server or the client, and for any account |
| | `ListCandidates` | the account's open candidates (#558): each one's base, staged lines, creation, last activity and expiry, and the server's idle rule |
| | `GetConfig` | interfaces, CGNAT pools and the CGNAT log export as fields (#558, phase 2), and the routing half - static routes, BGP, OSPF, OSPF6, IS-IS and each interface's IGP settings (phase 3) - of the running configuration or of a candidate |
| | `SetConfig`, `DeleteConfig` | one object of a candidate set to the fields given, or removed - an interface, a pool, the log export, a static route or a BGP neighbour: the server writes the VTY lines and stages them as `Edit` does |
| | `GetSchema` | the whole configuration grammar as the prompt's `?` answers it - every mode, statement, argument, `no` form and value source - versioned by release and digest |
| | `ValidateLines` | lines checked by the VTY in a throwaway candidate, each accepted or refused with its reason |
| OperationalService | `GetVersion`, `GetSystem`, `ListInterfaces`, `ListPools`, `GetSubscriber`, `GetSessionsSummary`, `ListBgpNeighbors`, `ListOspfNeighbors`, `ListIsisAdjacencies`, `ListVrrp`, `ListBundles` | the readers behind the matching `show`; `GetSystem` carries `show system uptime`'s clocks (0.148.7, #534): `boot_time`, `dataplane_started`, `control_plane_started`, `vty_started`, `release_installed_at` (unset when not read), `last_boot_reason` (only as recorded: `system reboot by <account>`, or `not recorded`) and `dataplane_restarted` |
| | `ListPolicies` | `show policy` (#538): every policy-statement, its terms with their statements and `then`, its `default-action`, its `policy-options` block as typed, and where the running routing applies it |
| | `GetPolicyCounters` | `show policy <name>`'s hits (#538): per route-map a policy is in - its `attach_point` as `test policy ... at` says it, every place applying it (`applied_at`), the daemon that runs it - how many routes each term matched; `counted` false when the daemon reported no such map, `compiled` false for a term with no clause at that use. An empty `policy` is every policy, each map once; a policy that does not exist is `NOT_FOUND` |
| | `TestPolicy` | `test policy <p> route\|rib <prefix> ... at <use>` (#538): `attributes` describe the route, as the words `route` takes; `rib` reads it off the box instead, with `vrf` and `neighbor`. Answers the verdict, the deciding policy and term, every step, the route before and after the sets, what was assumed and what is refused there. A use or an attribute it does not take is `INVALID_ARGUMENT`, a policy that does not exist `NOT_FOUND` |
| | `Show` | any `show`, `ping`, `traceroute` or `mtr`, as text (not `monitor`: it redraws a terminal, and a call has none - the telemetry stream is the API's live view) |
| | `GetLogging` | `show cgnat logging` (#555): each log export - syslog and IPFIX over TCP, IPFIX over UDP - its collector, who carries it (the log container or the dataplane), its connection, its counters, its spill and whether it is delivering; the box-wide drop counters; the screen itself in `text`. See [The log export](#the-log-export) |
| | `Clear` | `clear ...` (#558, phase 5): interface counters (one or every), a CGNAT subscriber's sessions, a BGP session (hard, soft, soft in or out), OSPF's process or one adjacency, IS-IS adjacencies; the VTY's line run, its output returned. See [Actions and the box](#actions-and-the-box) |
| | `LookupTranslation` | `show cgnat translation <public> <port>` (#558, phase 6): the subscriber behind it - an IPv4 address, or a NAT64 subscriber's prefix - its pool and block, and whether the answer holds for any instant (`SOURCE_FORMULA`) or only now (`SOURCE_RESERVE`, `SOURCE_DYNAMIC`). See [The CGNAT's screens](#the-cgnats-screens) |
| | `ListTop`, `GetDrops`, `GetCgnatMemory`, `ListBlocks`, `GetIdentity`, `VerifyHistory`, `ListAlgs`, `ListForwards`, `GetHaStatus` | `show cgnat top`, `drops`, `memory`, `blocks`, `identity`, `history verify`, `alg`, `forward` and `ha` (#558, phase 6), each as fields and its screen in `text`. See [The CGNAT's screens](#the-cgnats-screens) |
| | `ListOspfInterfaces`, `ListIsisInterfaces`, `GetHardware`, `GetRoutingLogging`, `GetSnmpFile` | `show ospf interface`, `show isis interface`, `show hardware`, `show logging` (FRR's, per daemon), `show snmp mib` and `show snmp zabbix-template` (#558, phase 7), each as fields and its screen in `text`; `GetSnmpFile` returns the file itself. See [The rest of the screens](#the-rest-of-the-screens) |
| | `Ping`, `Traceroute`, `Mtr` | `ping`, `traceroute`, `mtr` (#558, phase 7), streamed: a `ProbeEvent` per line the tool prints, with its reply, hop or report row read, then `end`. See [The probes](#the-probes) |
| TelemetryService | `Subscribe` | interface counters from the statistics segment at an interval, CGNAT totals, configuration events - each `Event` with the `revision` it left and the commit's `comment` (#533) - and, with `logging_events`, the log export's `log-...` events (#555) |
| | `GetHistory` | the counters the server sampled, a minute apart, for the last 24 hours (#533): interfaces, CGNAT totals, workers, memory; streamed, a sample a message |
| | `WatchLogging` | `monitor cgnat logging` (#558, phase 5): each block event the box exports, as a stream of `LogEvent`, with the monitor's filters and its delivery lines |
| RouteService | `ListRoutes` | FRR's RIB as `show ip route` reads it (#533): every VRF and both families, a page at a time from one snapshot, filtered by VRF, family, prefix (and what is inside it), protocol and selected |
| | `GetRouteSummary` | `show ip route vrf all summary`: per VRF, family and protocol, the routes in the RIB and in the FIB |
| SessionService | `WhoAmI` | the account the client certificate names, and the role the VTY login gives it (#533) |
| SystemService | `Reboot`, `Shutdown` | `system reboot`, `system shutdown` (#558, phase 5); refused while unsaved or inside a confirm window unless `force` |
| | `RestartDataplane` | `system dataplane restart`, run on the host detached; the reply when it finished |
| | `Upgrade`, `GetUpgradeStatus` | `system upgrade <version>` as a stream of phases, ending before the API restarts; the last run read back after it |
| grpc.health.v1.Health | `Check`, `Watch` | `SERVING` for `""` and each prisma.v1 service; `NOT_SERVING` for `OperationalService` and `TelemetryService` while the dataplane's statistics segment does not answer; `prisma.v1.LogExport`, the log export (#555): `NOT_SERVING` while a configured stream is stalled (see [The log export](#the-log-export)); `NOT_FOUND` for a service not served |

`GetSystem` also carries, since #533, the host's RAM as numbers
(`memory_total_bytes`, `memory_used_bytes` - MemTotal less MemAvailable, so the
dataplane's hugepages count as used - and `memory_hugepages_bytes`) and a
`WorkerLoad` per dataplane worker; `Interface` carries `speed_bps`, the link
speed the dataplane reports (0 when it reports none).

### A worker's load

A polling worker's CPU is 100% whatever it carries, so `WorkerLoad` says what
the dataplane counts:

* `vectors_per_call` and `loops_per_second` are VPP's own
  (`/sys/vector_rate_per_worker`, `/sys/loops_per_worker`), updated by the
  dataplane every 10 seconds; packets per graph call is near 0 idle and 256
  when every call takes a full frame.
* `busy_ratio` is the share of the worker's node time its graph took over
  `busy_window_ms`: the clocks `show runtime` gives the nodes that run only
  with packets, over every node's, the input nodes' polling included. It
  leaves out the receive inside the input node - that shares its clocks with
  the polling - so a worker that cannot keep up reads below 1: in
  `lab/api-grpc.sh` a worker saturated by a packet generator (256 vectors a
  call) read 0.49, the same worker polling an idle stream 0.0.
* A worker running under 1,000 loops a second waits for interrupts rather
  than polling: it reads 0 busy and 0 vectors, since the dataplane does not
  update its vector rate while it waits.

A first measure of busy as one less the loop rate over the idle loop rate was
refuted in the lab: a worker's idle rate depends on what it polls (6.7 million
loops a second polling nothing, 750 thousand polling one packet generator), so
no single idle figure holds.

### The routing table

`ListRoutes` is built for a full table. The first call (no `page_token`) asks
zebra for the routes the filters name - one `show ip route vrf <name> ...
json` a VRF and family, `default` first - and reads its answer as it streams,
a route at a time, into a snapshot file of the routes process (one process per
account, apart from its operational one); its first page is answered as soon
as it is read. Each `next_page_token` continues the same snapshot from the byte
the page before ended at, so a table that changes while it is paged is paged
once, without holes or repeats. `total` is set, and `total_known` true, once
the snapshot has been read whole.

Measured in `lab/api-grpc.sh` with 200,001 routes in zebra's RIB, paged 5000
at a time: the first page in 1.0 s, all of them in 12.4 s; the server's
largest process peaked at 96,592 kB resident, vtysh - which passes zebra's
answer on as it comes - at 22,252 kB, and zebra at 196,176 kB while it
answered. A page holds at most 5000 routes (default 1000), well under a 4 MB
message.

* A snapshot nobody pages for 10 minutes is dropped; its token then answers
  `FAILED_PRECONDITION`, and the client starts again. A routes process holds
  four snapshots at most, the oldest dropped first.
* A page waits up to 60 seconds for the snapshot to reach it, then answers
  what it has with a token at the point it reached.
* If zebra stops answering part-way, the routes read are handed out and the
  next call is `UNAVAILABLE` with the reason - a short table is never passed
  off as the whole one.
* A VRF zebra does not have is `NOT_FOUND`; a protocol FRR does not name, a
  prefix that is not one, or a family other than 4 or 6 is
  `INVALID_ARGUMENT`.

### The history

The server samples the counters once a minute and keeps 24 hours of them:
every interface's (up to 256 interfaces; the 257th is not recorded), the
CGNAT's totals, each worker's load and the memory used - in arrays of machine
words, some 80 KB an interface a day. Counters are the dataplane's running
totals, as `Subscribe` sends them: a client takes differences, and a total
smaller than the one before is a dataplane that restarted.

It is written every five samples, and when the server stops, to
`/run/prisma-routing/api/history.json` - the host's `/run`, which the
management container shares - so a restart of the server or of its container
(an upgrade does both) reads it back, and a client's graphs go on. A reboot
empties it, as it zeroes the dataplane's counters.

### The log export

`GetLogging` (#555) is read from `show bpa logging` - the screen the VTY
prints as `show cgnat logging` - and from nothing else, so the API and the
prompt can never disagree; the screen is in `LoggingState.text` as read. The
server reads it on every call and every 10 seconds on its own, which is what
the health service and the events need.

**Delivering.** A stream is `delivering` unless it is configured, has had
something to send, and has got nothing out for **60 seconds**:

* a TCP stream (syslog, IPFIX over TCP) has something to send while bytes are
  pending in memory or its spill file is not empty, and gets something out
  when its `bytes_sent` grows;
* the UDP export has something it failed to send when its `send_failures` or
  `dropped` grow, and gets something out when its packets (the log container)
  or records (the dataplane) grow.

A stream with nothing to send is delivering whatever its connection: a
syslog collector that is down is reported the moment a line has to go there.
`stalled_reason` says what is wrong; `last_progress` when the stream last got
something out, or last had nothing to send. The health service answers
`NOT_SERVING` for **`prisma.v1.LogExport`** while any configured stream is
not delivering, and when the dataplane does not answer the screen - under a
name of its own and not `OperationalService`'s or `""`: a stalled collector
is not a box whose reads fail, and a client that stops calling an
OperationalService marked `NOT_SERVING` would stop reading the state that
says what is wrong.

**Counters and gauges.** Each field's comment in the contract says which. A
counter counts from the dataplane's start and is the stream's **current
carrier's**: when a stream moves between the log container and the
dataplane (#550) it may jump or start again, and a client takes a decrease as
a reset. `exhaustion_repeats_not_reported` is expected under exhaustion (one
record a subscriber per 10 s, #549), not a loss; `transmit_failed` counts
every sender on the collector's interface, forwarded traffic included.

**Events**, with `SubscribeRequest.logging_events` - as `Event`s whose `kind`
starts `log-` and whose `text` names the stream: `log-connected`,
`log-disconnected`, `log-carrier`, `log-spill` (the spill file started),
`log-spill-full` (bytes dropped over the cap), `log-stalled`,
`log-delivering`. A reading every 10 s makes an event up to that late.

### The CGNAT's screens

The rest of `show cgnat` (#558, phase 6) is typed by reading the screen
itself. Eight of these screens - `translation`, `top`, `drops`, `blocks`,
`identity`, `history verify`, `alg`, `forward` - are the dataplane's own
commands, which the VTY forwards word for word; the rpc runs the same VTY
line in the account's own process and reads its answer into fields, so a
typed answer and the screen an operator reads are one reading, never two.
`memory` and `ha` are drawn by the VTY from data it reads itself, and the rpc
takes that data and the screen drawn from that one reading. Every response
carries the screen, verbatim, in `text`.

* **Who may call them.** Any account, with the access the VTY gives it:
  the read runs in the account's own process, so an account that cannot
  open the dataplane's socket at the prompt is refused here too, as
  `UNAVAILABLE`. A dataplane that does not answer is `UNAVAILABLE`; one that
  refuses the command does so in its own words, which the call carries.
* **`LookupTranslation`** checks its arguments before anything runs: an
  address that is not IPv4 or a port over 65535 is `INVALID_ARGUMENT`; an
  address no pool owns is `NOT_FOUND`, with the dataplane's sentence. Its
  `now_only` is the answer's weight: a dynamic block or a reserve block was
  somebody else's before, and for a past instant the RFC 8158 log says whose.
* **NAT64 pools** (#485): a subscriber is its delegated prefix
  (`2001:db8:1200:ab00::/56`) in `Translation.subscriber` and
  `TopSubscriber.subscriber`, and a forward's `private_address` is IPv6.
* **Counters and gauges** are marked field by field in the contract. The
  counters count from the dataplane's start; `GetDrops` lists only the
  counters that are not zero, the ones that mean a packet did not get through
  marked `dropped`.
* **Not an identity.** `GetIdentity` answers how many identities the box
  holds, when it read them and RADIUS accounting's counters - never an
  entry, which no screen prints either (#21). Its `read_at` is the
  dataplane's own clock as it prints it.
* **`GetHaStatus`** compares against the peer's copy at its default path;
  `agree` is true only when that copy was read and no pool diverges, never
  on a question left unanswered. `show cgnat ha peer <file>`, a path on the
  box's own disk, stays a screen through `Show`.
* **Widths.** `show bpa top` and `show bpa drops` printed through vppinfra
  widths, which cut a value longer than its column: a pool of more than
  twelve letters, more than 99,999 ports, a counter of thirteen digits. The
  dataplane pads those columns now and never cuts them, so neither the
  screen nor the fields carry a truncated value.

### The rest of the screens

`show ospf interface`, `show isis interface` and `show hardware` (#558,
phase 7) are drawn by the VTY from one reading of the box - the Shell's
`ospf_interface_facts()`, `isis_interface_facts()` and `hardware_facts()` -
and the rpc carries that reading as fields and the screen drawn from it in
`text`, so the two cannot disagree. `show logging` is FRR's own screen,
which the VTY passes through; `GetRoutingLogging` reads it back per daemon.

* **"Not answered" is not "nothing".** `answered` false on
  `ListOspfInterfaces` and `ListIsisInterfaces` is the daemon not answering;
  an empty list with `answered` true is a box running the protocol on no
  interface. `GetHardware`'s `dataplane_answered` false means a card the
  dataplane took is missing from `nics` with nothing to say so.
* **Sentinels.** FRR's `-` reads as `-1` for a cost or a metric and as `0`
  for an MTU; `dataplane_mtu_known` false is an interface the dataplane has
  no MTU for (the management port), not an MTU of 0. `punt_known` false is
  a dataplane that could not be asked whether it punts IS-IS.
* **`GetSnmpFile`** returns the MIB or the Zabbix template with the
  enterprise number the agent serves under filled in, the bytes `show snmp
  mib` prints, a file name and a media type to save it as. With no number
  yet, or the file missing from the box, it is `FAILED_PRECONDITION` with
  the VTY's words.

### The probes

`Ping`, `Traceroute` and `Mtr` (#558, phase 7) run the probe the VTY runs -
from the management plane, through the host's door on a box - and stream it:
one `ProbeEvent` per line the tool prints, `line` verbatim, with `reply`
(ping), `hop` (traceroute) or `mtr_hop` (a row of mtr's report, which mtr
prints at the end of its run) when the line is one, and a last event with
`end`: the tool's exit status, `stopped_at_deadline`, and ping's summary.

* **The VTY decides.** The request is handed to the VTY's own `ping`,
  `traceroute` or `mtr` statement in the account's process: a host, count or
  source it refuses is `INVALID_ARGUMENT` with its words, a tool the box
  lacks `FAILED_PRECONDITION` naming the package. `count` is 1-100, 0 the
  VTY's default of 5; the box stops a tool still running after 300 s
  (`stopped_at_deadline`, exit status 124).
* **Cancelling the call stops the probe.** The account's process is told,
  closes the tool, and the server reads the stream to its end before the
  process takes another call.
* **One at a time per account**, as at its console: a probe holds the
  account's console process, not its operational one, so `GetVersion` and
  the other reads answer while it runs.
* **Exit status.** ping exits 1 when no reply came; `end.received` and
  `end.loss_percent` say how many did. `exit_status` -1 is a status nobody
  saw, never a success.

### Why a change was refused

Every refused `OpenCandidate`, `Edit`, `Commit`, `Confirm`, `Rollback`,
`Save` and `CloseCandidate` carries `Diagnostics.reason` in its trailer
(#558); `Check` answers the same code in `CheckResponse.reason` when it
does not pass, or when a Commit would meet a gate.

| `reason` | Status | What happened, and what the client does |
|---|---|---|
| `REASON_STALE_BASE` | `FAILED_PRECONDITION` | the running configuration moved since the candidate was opened; `running_revision` is where it is now - open a new candidate and apply again |
| `REASON_PERMISSION` | `PERMISSION_DENIED` | the account's role does not allow it |
| `REASON_CANDIDATE_EXPIRED` | `NOT_FOUND` | the server closed the candidate, unused for `idle_seconds` |
| `REASON_CANDIDATE_UNKNOWN` | `NOT_FOUND` | not this account's candidate, or one it closed |
| `REASON_LIMIT` | `INVALID_ARGUMENT` at entry, `FAILED_PRECONDITION` at a commit | a value longer than its field: `limit_field`, `limit`, `limit_size` (bytes) |
| `REASON_GATE_REMAP`, `_DIVERGE`, `_CUTOFF` | `FAILED_PRECONDITION` | the VTY's gates; `gate_items` lists what each is about - commit again with the matching `acknowledge_*` |
| `REASON_CONFIRM_PENDING` | `FAILED_PRECONDITION` | a confirm window could not be confirmed, or a revert failed and the box is still on the unconfirmed configuration - `GetPendingConfirm` |
| `REASON_GRAMMAR` | `INVALID_ARGUMENT`, `FAILED_PRECONDITION` | a line the VTY's grammar does not take, or a commit of a batch that had one |
| `REASON_VALIDATION` | `FAILED_PRECONDITION` | the candidate refused for what it means (a pool without its prefixes) |
| `REASON_APPLY_FAILED` | `FAILED_PRECONDITION` | applied and failed (FRR refused a line); the box rolled back |
| `REASON_USAGE` | `FAILED_PRECONDITION`, `INVALID_ARGUMENT` | the request's own words were wrong: `confirmed` outside 1-60, or a typed edit with an unknown field, area or transport, or one its fields cannot write (a UDP collector with no source) |
| `REASON_OTHER` | any | none of the above: read the message |

The server reads the code from the VTY's own sentences, and
`test/test_apiserver.py` holds that table to the VTY's source, so a sentence
reworded there turns the suite red instead of quietly answering `OTHER`.
`lab/api-grpc.sh` provokes each of grammar, validation, limit, stale base,
permission, unknown, the re-map gate and expiry against a real server.

### The configuration as fields

`GetConfig`, `SetConfig`, `DeleteConfig`, `GetSchema` and `ValidateLines`
(#558, phase 2) cover what a UI edits most - each interface's block, each
CGNAT pool, and the CGNAT log export's collectors - as fields, and every
configuration mode as grammar. They are the line form's
other face, not a second configuration:

* **Reads come off the VTY's own Config.** `GetConfig` with no
  `candidate_id` reads the running configuration (its `revision` is
  GetRunning's); with one, that candidate. The fields are what `show
  running-config` prints, never text parsed again by the server, so the two
  cannot disagree. A field at its default is a statement the block does not
  have: `mtu` 0, `vrf` 0, `cgnat_role` "", a pool's unset `vrf` (every VRF,
  which is not VRF 0).
* **Edits are lines.** `SetConfig` sets one object - an `interface`, a
  `pool`, or the `logging` export - to the fields given, against what the
  candidate holds now. The server writes the lines an operator would type
  (`description uplink`, `no mtu`, `no ipv4 address 10.0.0.1/24`,
  `logging udp <c> src <s> port <n>`), stages them exactly as `Edit` does,
  and returns them in `lines`, with the `path` they went to. `fields`
  limits the change to the fields named (`"ports"` for `port_first` and
  `port_last`, `"vlan"` for the tags); empty changes every field, so a
  client sending a partial object names what it means. A collector the
  `logging` object does not list is left alone; one listed with
  `configured` false is disabled. Unchanged fields write nothing.
* **Every refusal of the line form applies**, with its `Diagnostics.reason`:
  the VTY refuses `mtu 50` at entry exactly as at the prompt
  (`INVALID_ARGUMENT`), and a commit of the candidate meets the same gates.
  A request whose own fields cannot become a line answers
  `INVALID_ARGUMENT` with `REASON_USAGE`.
* **`GetSchema`** is the WHOLE configuration grammar, generated from the
  tree the prompt's `?` answers from - every mode (26: config, nat, cgnat,
  pool, system and its levels, policy-options and its levels, user,
  snmp-user, the interfaces, router static/bgp/ospf/ospf6/isis, rpki,
  route-map), each with the `path` to type its statements at, how it is
  entered and the roles that change it. Each statement has its words, help,
  its forms (`<collector> src <source> [port <n>] | disable` is two), each
  argument's kind (`integer` with its range, `ipv4_prefix`, `ipv6_prefix`,
  `address`, `prefix`, `choice` with its values, `text`...), whether it is
  optional, whether it may repeat, its `no` words, its `source` when the VTY
  completes it from the box (`interfaces`, `pools`, `neighbours`,
  `next_hops`, `prefix_lists`, `route_maps`...), and whether GetConfig types
  it. Since 0.149.24: `words` are literal keywords only, and each
  argument's `position` says how many words come before it (`neighbor
  <address> remote-as <asn>` is words `neighbor`, `remote-as`, with
  `<address>` at 1); a `|` between a placeholder and words is one form per
  kind, never a choice holding a placeholder; a keyword-led part (the
  keyword and the values sharing its optional/required standing) has a
  `group` and is `unordered` - FRR prints such parts in an order of its own,
  so match them in any order after the words, the other arguments in
  theirs; `many` is one or more values to the end of the line. Every line of
  a running box's configuration matches a statement, the box-owned ones
  aside (`lab/api-grpc.sh` holds GetRunning to it with the product's own
  matcher, typedcfg.unmatched_lines). `release` and `digest` version it:
  equal digests are equal grammars.
  `with_values` adds what each source holds on the box now (that asks the
  dataplane and FRR, so it is off by default). It replaces parsing
  docs/api-coverage.md, whose format stays a contract until clients have
  moved.
* **The box's own lines.** A running configuration holds lines no grammar
  takes because they are the box's - FRR's `log syslog <level>`, `service
  integrated-vtysh-config`, a `vrf prisma-*` block and its `exit-vrf` - and
  the VTY marks them "not checked". `TypedConfig.box_owned` lists the ones a
  configuration has, with why, on every read; `ConfigSchema.box_owned` lists
  their patterns. A UI shows them read only, on purpose.
* **`ValidateLines`** opens a candidate nobody sees, feeds each line at
  `path`, answers each accepted or refused with the VTY's words and its
  reason, and discards it: nothing is committed and the running revision
  does not move. Lines are fed in order, so a later line sees the earlier
  ones.

`lab/api-grpc.sh` takes an interface, a pool and the log export each through
SetConfig, Commit and GetConfig, and holds the typed read to GetRunning's
text.

### The routing configuration as fields

The routing half (#558, phase 3) is read through the same `GetConfig`, by
its areas - `static`, `bgp`, `ospf`, `ospf6`, `isis`, `interface_routing` -
or with every area when none is named:

* **It is FRR's own text, read block by block.** This VTY does not model
  routing as it models a pool: the configuration holds the FRR statements
  themselves, typed at the prompt or read back from FRR, and `show
  running-config` prints them. GetConfig reads those statements through the
  VTY's own block reader, so a statement is in the block the screen shows it
  in, whether it was read back indented or typed flat in this session.
* **A field for what a UI edits; the rest verbatim.** Every block - a
  `router bgp`, a family, a neighbour, an OSPF area, an interface's IGP
  settings - has an `other` list: the statements no field takes, exactly as
  written. Nothing is dropped, and a field added later is a field, not a
  format change.
* **Static routes** are the ones `router static` shows (`line` is the route
  as that block takes it, with the description this box keeps), and each
  VRF's own, with its `vrf`. `distance` 0 and `tag` 0 are not stated.
* **BGP:** each `router bgp` (its asn as written, its VRF, router-id), each
  neighbour or peer-group (remote-as as a number or `internal`/`external`,
  description, update-source, timers, ebgp-multihop, shutdown, bfd, its
  peer-group) and each family's networks, aggregates, redistribution and
  what it says about each neighbour (activate, route-maps and prefix-lists
  in and out, maximum-prefix, next-hop-self, soft-reconfiguration,
  default-originate). A route-map named `PO-...` is a routing policy's
  compiled map (#458). **A password is never read**: `password_set` says
  whether one is.
* **OSPF and OSPF6** (`version` 2 and 3): router-id, networks, areas with
  their type (`area_type`: normal, stub, nssa) and no-summary, the passive
  posture (`passive_default`, `passive`, `not_passive`), redistribution and
  the `default-information` statement. **IS-IS:** its tag, NETs, is-type,
  metric-style and redistribution. **`interface_routing`:** what each FRR
  `interface` block says to the IGPs - OSPF area, cost, network type, hello
  and dead, passive; OSPF6 area and cost; IS-IS instances, circuit-type,
  metric, passive.
* **Edits: static routes and BGP neighbours.** `SetConfig` with a
  `static_route` writes the one line `router static` takes - a route IS its
  prefix and next hop, and typing the pair again replaces it whole,
  description included, so the line carries every attribute, the ones not
  in `fields` kept from the candidate. A VRF's route is not set here
  (`REASON_USAGE`): it is a line in its `vrf` block, through Edit. With a
  `bgp_neighbor` (`BgpNeighborEdit`: the router's asn and vrf, the
  neighbour, a write-only `password`, and per family its settings) it
  writes only what differs from the candidate - router-level statements,
  then each family entered, set and left. A family listed is set whole: a
  route-map it does not name is removed. `fields` names what to change
  (`timers` for keepalive and holdtime, `families` for the families); a
  `password` not given is never cleared. `DeleteConfig` removes a route
  (`area` "static", its `prefix` and `next_hop`) or a neighbour (`area`
  "bgp", `name`, `asn`, `vrf`) - statement by statement, read off the
  candidate, each family's first and `remote-as` last, because a bare `no
  neighbor X` is staged by this VTY as a statement of its own and, with a
  family statement, refused by frr-reload (measured, FRR 10.7.1). The lines
  a reply shows mask a BGP password, as `show running-config` does: the box
  is given the real ones. `default_ipv4_unicast` says whether FRR activates
  a neighbour in ipv4 unicast without an `activate` line (its default; it
  prints no such line then). The other routing statements are written as
  lines through Edit, which GetSchema describes.

`lab/api-grpc.sh` configures a static route, BGP, OSPF, OSPF6, IS-IS and an
interface's OSPF settings at the prompt, holds GetConfig's routing areas to
GetRunning's text block by block, and takes a static route and a BGP
neighbour through SetConfig, Commit, GetConfig, the VTY's text and
DeleteConfig.

### The rest of the configuration as fields

#558's fourth phase reads every other part of the configuration through the
same `GetConfig`, by area - after it, no configuration statement is reachable
only as text (docs/api-coverage.md says, statement by statement, how each is
read and written):

* **`system`:** the hostname, the resolvers in order, SSH (port,
  listen-address, permit-root-login, password authentication), the API's
  listener, the SNMP agent (enabled, listen-address, port, the enterprise
  number the MIB is rooted at, how many communities there are, each trap
  host as its address and whether it is an inform), the card the dataplane
  is given, the CGNAT's memory, and `system` > `cpu` / `memory` statement by
  statement as pairs (they take effect at the next boot).
* **`users`:** each account's name, role (`""` is read only), how many SSH
  keys it has and whether a password is set. **`snmp_users`:** each SNMPv3
  user's name and its auth and privacy protocols.
* **`policy`:** policy-options (#458) as the commit's own parser reads it -
  the named prefix-lists, communities, large and extended communities and
  AS paths, and each policy-statement with its terms in order: the `from`
  conditions by kind (`prefix-list`, `community`, `route-filter`,
  `protocol`, `policy`...), the `set` changes and the decision. A line the
  parser cannot read is in `unread`, with why.
* **`route_maps`, `frr_lists`, `routing_attachments`:** what is written for
  FRR at the top of the routing half - the route-maps an operator wrote
  (each entry's action, sequence, match and set statements, call, on-match;
  the compiled `PO-` maps are policy-options' and are in `policy`), the
  prefix-lists with each entry's ge/le, the community, large-community and
  AS-path lists, and where a route-map or a policy is attached outside a
  protocol's block (`ip|ipv6 protocol ... route-map`, `fib-policy`).
* **`rpki`:** the validator caches (transport, host, port, user, source,
  preference) and the timers. **`vrfs`:** the Linux VRF devices (#540).
  **`vrrp`:** each virtual router, per interface and VRID, its addresses,
  priority and tracks. **`bundles`:** each LACP bundle's members, mode, rate
  and hash. **`cgnat_globals`:** the `nat` > `cgnat` statements other than
  pools, roles and the log export - the session timeouts, the block
  hold-down, the identity source, the session table and the CGNAT's memory.
* **The routing blocks' knobs.** What phase 3 kept verbatim in a block's
  `other` - `bgp log-neighbor-changes`, a neighbour's `maximum-prefix`,
  OSPF's `auto-cost reference-bandwidth`, IS-IS's `set-overload-bit` and the
  like - is read as `settings` since this phase: the statement's keywords as
  the key, the rest as the value (`""` for a flag). What no key takes is
  still in `other`.
* **Secrets are never read.** An account's password is `password_set`, a
  key is counted, an SNMP community is counted, a trap host's community and
  an SNMP user's passphrases are left out, an IS-IS password is `<set>`, a
  RADIUS identity secret is `identity_radius_secret_set`. They are set as
  lines through Edit; no typed field takes one, and naming one in
  `SetConfig.fields` is `INVALID_ARGUMENT`.
* **Edits: the system settings and the VRF devices.** `SetConfig` with a
  `system` writes only what differs, at the configuration level; its fields
  are named by block and key - `hostname`, `dns_servers`, `ssh.port`,
  `ssh.listen_address`, `ssh.permit_root_login`,
  `ssh.password_authentication`, `api.enabled`, `api.listen_address`,
  `api.port`, `snmp.enabled`, `snmp.listen_address`, `snmp.port`,
  `snmp.enterprise_number` - and **name them**: an empty `fields` applies
  every field, so an unset `dns_servers` would remove the resolvers. An
  empty listen-address removes the statement; a zero port is left alone.
  With a `vrf_device` it writes `vrf <name> table <id>`; `DeleteConfig` with
  `area` "vrfs" and the `name` removes one. The rest is written as lines
  through Edit, which GetSchema describes: `read_typed` says GetConfig reads
  a statement as a field, `typed` that SetConfig writes it too.

`lab/api-grpc.sh` holds the running configuration's policy, VRF device,
accounts and API listener to GetRunning's text, stages a route-map, FRR
lists, an attachment, an SNMP user and community, an OSPF knob and CGNAT
globals in a candidate and holds GetConfig of that candidate to its own
text, takes the system settings and a VRF device through SetConfig,
GetConfig and DeleteConfig, and searches the typed answer for the secrets
it staged.

### Actions and the box

Since 0.149.25 (#558, phase 5) nothing the VTY does is out of the API's reach:
[api-coverage.md](api-coverage.md) lists no path as `none`.

**Clears.** `OperationalService.Clear` takes a `target` and, where it needs
one, a `name` - an interface, an inside address, a neighbour, an OSPF router
id, an IS-IS system id, always one word - and runs the VTY's own `clear`
line, which `command` returns. Two of them were added to the VTY in the same
release: `clear interface counters [<interface>]`, which resets the
dataplane's counters (every count the telemetry reports starts again from
there), and `clear cgnat
subscriber <A.B.C.D>`, which ends every session of one subscriber on every
worker. Its port block is not taken back by the clear: idle, it is held down
and released by the expiry process with its natEvent 17, as an expiry would,
so the log says what an expiry says. The interface counters and the routing
clears are an administrator's, the subscriber clear a CGNAT operator's too -
the VTY's roles.

**The log, live.** `TelemetryService.WatchLogging` is `monitor cgnat
logging`: the first message is `STARTED` (whether the events come off the
watch ring, and whether pool names are known), then a `BOOT`, then each
`ALLOC`, `RELEASE` and `EXHAUSTED` as the box exports it - subscriber, public
address, port range, pool by name, identity - at the time its record
carries, and every 10 s a `DELIVERY` per stream that moved (what it sent,
what it lost, what waits) and per box-wide loss that grew. A reader that
falls behind gets a `LOST` with the count. It reads the observation ring the
dataplane overwrites and nobody consumes: it takes nothing from the log
container and the dataplane does not wait for it. The filters are the
monitor's: `subscriber`, `public_address` with `port`, `event`, `pool`,
`seconds`, `raw`. Who may watch is who may read the segment
(`/dev/shm/prisma-bpa-log`, mode 0640): an account that may not gets
`PERMISSION_DENIED` with the monitor's own words.

**The box.** `SystemService` is the VTY's `system` statements, with the
VTY's permissions - today root's: the host's door for a reboot, an upgrade
or a dataplane restart opens to uid 0 only (#212 records why no other
account is granted it), so an administrator that is not root is refused
with `REASON_PERMISSION` there as at the prompt.

* `Reboot` and `Shutdown` refuse with `FAILED_PRECONDITION` and
  `REASON_UNSAVED` while the running configuration differs from what the
  next boot loads, and `REASON_CONFIRM_PENDING` inside a `commit confirmed`
  window - what the VTY warns of before either - unless `force`. The reply
  comes before the box goes down.
* `RestartDataplane` and `Upgrade` run on the host, detached, as units of
  their own (`prisma-routing-api-dataplane`, `prisma-routing-api-upgrade`)
  whose output goes to `/run/prisma-routing/api/<kind>.log`: an upgrade
  recreates the container the API runs in, so it cannot run inside it. A
  second one while the first runs is `REASON_BUSY`.
* `Upgrade(version)` streams `STARTED`, `DOWNLOAD`, `VERIFY`, an `INSTALL`
  per row the package prints, `INSTALLED`, and last `API_RESTART`, with
  `api_away_seconds`: the stream ends there, before the management API
  restarts on the new release. Reconnect after it and read `GetVersion`;
  `GetUpgradeStatus` reads the run back from the host's log, whatever API
  started it. A `FAILED` carries the run's output and its reason. An empty
  `version` installs nothing: one `CHECKED` names the newest release.

Measured in lab/api-grpc.sh with stand-ins for `systemctl` and `systemd-run`
- a container is no box to restart; the real reboot, shutdown, dataplane
restart and upgrade are measured on one.

## Limits a client meets

* **A request a session does not answer in 300 seconds** fails with
  `UNAVAILABLE` ("no answer in 300s"); the session's process is left as it
  is, and the next request reaches it.
* **A candidate nobody has touched for 30 minutes is closed**, its process
  ended: the next call naming it is `NOT_FOUND` with
  `REASON_CANDIDATE_EXPIRED`, and the client opens a new one. Read the rule
  rather than assume it (#558): `idle_seconds` and `expires_at` on
  `OpenCandidateResponse`, `last_activity` and `expires_at` on every
  `EditResponse`, and `ListCandidates`, whose listing does not count as
  using a candidate. An account's process for operational calls is kept 5 minutes between
  uses and started again on the next.
* **One request at a time per process, and an account has three kinds.**
  Each candidate is a process; the account's operational reads
  (`GetVersion`, `GetSystem`, the lists) share one; and `Show` - the
  console, where a `ping` or a `traceroute` takes as long as it takes - has
  one of its own (0.148.6). So a status poll is never queued behind a
  console command or a commit: in `lab/api-grpc.sh`, `GetVersion` answered
  in 0.06 s while a `Show` ran 14 s. Two `Show`s of one account still run
  one after the other. `ListRoutes` has a process of its own too (#533),
  kept 10 minutes between uses, as long as its snapshots.
* **An event's revision is read when it is sent** (#533): the running
  configuration's id as the subscribing account reads it, so it is the id
  that account's `CommitResponse` and `GetRunning` carry. An account whose
  role sees less of the configuration (a read-only account sees no pool)
  reads a different id for the same configuration. When another commit lands
  while it is read, the event's revision is empty and the later commit's
  event carries it.

## Security

* **Mutual TLS only.** The server refuses to start without
  `/etc/prisma-routing/api/server.key`, `server.crt` and `clients-ca.crt`, and
  refuses a handshake without a client certificate that CA signed. That holds
  for a health check too: it needs a certificate the CA signed, though not
  one naming a box account.
* **The account is the client certificate's Common Name.** It MUST be an
  account on the box; it gets the roles the VTY login gives it (`prisma-admin`
  configures everything, `prisma-cgnat` the CGNAT, anyone else reads), and the
  configuration trail records it as the author. An account without the role
  is refused exactly as the VTY refuses it.
* **The management network only.** `api listen-address` is the management
  address and enabling the API without one is refused at the check. The host
  listens there and carries each connection, unopened, to the server on the
  backplane (`128.0.0.2:50051`); the management container has no network of
  its own.
* The host's actions the VTY takes through its doors are taken through them
  here too, as the account's uid: the API grants nothing the account's VTY
  session does not have.

### The certificates

The operator's own CA signs the box's server certificate and one client
certificate per account. With OpenSSL, on any machine:

```sh
# The CA that signs the clients the box accepts (keep ca.key off the box).
openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=prisma clients" \
  -keyout ca.key -out ca.crt
# The box's server certificate, with the address the client dials.
openssl req -newkey rsa:3072 -nodes -subj "/CN=prisma-api" -keyout server.key -out server.csr
printf 'subjectAltName=IP:10.40.200.166\n' > server.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -extfile server.ext -out server.crt
# The backend's client certificate: CN is the box account it acts as.
openssl req -newkey rsa:3072 -nodes -subj "/CN=webui" -keyout webui.key -out webui.csr
printf 'extendedKeyUsage=clientAuth\n' > client.ext
openssl x509 -req -in webui.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -extfile client.ext -out webui.crt
```

On the box, `server.key`, `server.crt` and the CA as `clients-ca.crt` go in
`/etc/prisma-routing/api/` (mode 0700, root), and the account the backend acts
as is created like any other (`user webui` in the VTY, in `prisma-admin` if it
configures). Then, in the VTY:

```
configure
api enabled yes
api listen-address 10.40.200.166
api port 50051
commit
```

## For the Rust backend (tonic)

The client compiles the contract with `tonic-build`; the `.proto` is vendored
from this repository at the release the backend is built against.

```toml
# Cargo.toml
[dependencies]
tonic = { version = "0.12", features = ["tls"] }
prost = "0.13"
prost-types = "0.13"
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }

[build-dependencies]
tonic-build = "0.12"
```

```rust
// build.rs
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .build_server(false)
        .compile_protos(&["proto/prisma/v1/prisma.proto"], &["proto"])?;
    Ok(())
}
```

```rust
use tonic::transport::{Certificate, Channel, ClientTlsConfig, Identity};
pub mod prisma { tonic::include_proto!("prisma.v1"); }
use prisma::{config_service_client::ConfigServiceClient, OpenCandidateRequest};

let tls = ClientTlsConfig::new()
    .ca_certificate(Certificate::from_pem(std::fs::read("ca.crt")?))
    .identity(Identity::from_pem(std::fs::read("webui.crt")?, std::fs::read("webui.key")?))
    .domain_name("prisma-api");
let channel = Channel::from_static("https://10.40.200.166:50051")
    .tls_config(tls)?
    .connect()
    .await?;
let mut cfg = ConfigServiceClient::new(channel);
let cand = cfg.open_candidate(OpenCandidateRequest { from_revision: None }).await?;
```

`domain_name` MUST match a name in the server certificate (`prisma-api` above),
or `subjectAltName` MUST carry the address dialled. The refusal detail is
`status.metadata().get_bin("prisma-diagnostics-bin")`, decoded as
`prisma::Diagnostics`.

## From a shell (grpcurl)

```sh
grpcurl -cacert ca.crt -cert webui.crt -key webui.key -authority prisma-api \
  -import-path proto -proto prisma/v1/prisma.proto \
  10.40.200.166:50051 prisma.v1.OperationalService/GetVersion
grpcurl -cacert ca.crt -cert webui.crt -key webui.key -authority prisma-api \
  -import-path proto -proto prisma/v1/prisma.proto \
  -d '{"command": "show cgnat pool"}' \
  10.40.200.166:50051 prisma.v1.OperationalService/Show
grpcurl -cacert ca.crt -cert webui.crt -key webui.key -authority prisma-api \
  -import-path proto -proto prisma/v1/prisma.proto \
  -d '{"policy": "UP", "prefix": "203.0.113.0/24", "attach_point": "import",
       "attributes": {"neighbor": "10.0.0.2", "community": "65001:10"}}' \
  10.40.200.166:50051 prisma.v1.OperationalService/TestPolicy
grpcurl -cacert ca.crt -cert webui.crt -key webui.key -authority prisma-api \
  -import-path proto -proto prisma/v1/prisma.proto \
  -d '{"vrf": "default", "protocol": "bgp", "page_size": 1000}' \
  10.40.200.166:50051 prisma.v1.RouteService/ListRoutes
```

The health check is gRPC's own, so a stock client works with the same
certificate - `grpc_health_probe -tls -tls-ca-cert ca.crt -tls-client-cert
webui.crt -tls-client-key webui.key -tls-server-name prisma-api -addr
10.40.200.166:50051`, or tonic's `tonic-health` client. Its contract is
[`api/health/v1/health.proto`](../api/health/v1/health.proto), gRPC's
`grpc.health.v1` field for field; a client using a health crate does not need
it.

`show policy route <prefix>` - which policy decided a route the box holds -
has no typed call: it is `Show`'s, as text, and the same answer `TestPolicy`
gives with `rib` at each use.

## How it is held

* `test/test_api_contract.py` reads the contract against the rules above
  without protoc: unique field numbers, no Rust keyword as a name, the services
  the issue asked for, and the health protocol number for number.
* `test/test_apiserver.py` drives the server's core without gRPC: the VTY's
  output read into diagnostics, the lines a call becomes, sessions in their
  own processes, a commit over a moved base, the readers, the telemetry
  arithmetic.
* `test/test_api_configure.sh` holds the host's listener to `api.env`, and
  nothing in `api.env` to anything but data.
* `lab/api-grpc.sh` drives every rpc over mutual TLS against a dataplane,
  reads the result through the prompt, streams pg0's counters while traffic
  runs, lists a policy applied on a neighbor, counts its terms in bgpd's map
  and tests a route through it, and is refused for an account without the
  role and for a client with no certificate. Since #533 it also pages a RIB
  of 200,000 routes and a VRF beside it, reads a worker busy under a packet
  generator and idle without one, matches a commit's event to its
  `CommitResponse`, reads the history back through a second server, and asks
  the health service.

**Not covered by a lab**: the host's socket and proxy in front of a running
management container (the lab runs the server directly), a client in Rust,
and a link speed other than 0 - neither the lab's packet generator nor an
`af_packet` interface reports one, so the conversion from the dataplane's
kbps is the unit test's. The first is held by `test/test_api_configure.sh`'s stubs; the second is
the web interface's repository's to hold.
