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

## Coming: #550 - the logging counters in the API

Not released. Since 0.149.14 every log export runs in the log container when
it is up; the logging counters (`show cgnat logging`) are not in the API yet.
When they reach an API client - drop counters in `SystemInfo` or the
telemetry stream, a health status for the export - it is announced here
before the release that carries it.

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

* **Contract:** unchanged.
* **Fixed (#546):** after an upgrade the API could stay unreachable for about
  ten minutes: the backplane kept a stale route to the old container. Since
  0.149.4 the API answers again as soon as the new container is up.
  **Client workaround before 0.149.4:** a connection refused right after an
  upgrade can persist; retry with backoff for up to 15 minutes.

## 0.149.3 - an unknown link speed is 0, not 4.3 Tbps

* **Contract:** unchanged.
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
