# The management API (prisma.v1)


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
| OperationalService | `GetVersion`, `GetSystem`, `ListInterfaces`, `ListPools`, `GetSubscriber`, `GetSessionsSummary`, `ListBgpNeighbors`, `ListOspfNeighbors`, `ListIsisAdjacencies`, `ListVrrp`, `ListBundles` | the readers behind the matching `show`; `GetSystem` carries `show system uptime`'s clocks (0.148.7, #534): `boot_time`, `dataplane_started`, `control_plane_started`, `vty_started`, `release_installed_at` (unset when not read), `last_boot_reason` (only as recorded: `system reboot by <account>`, or `not recorded`) and `dataplane_restarted` |
| | `ListPolicies` | `show policy` (#538): every policy-statement, its terms with their statements and `then`, its `default-action`, its `policy-options` block as typed, and where the running routing applies it |
| | `GetPolicyCounters` | `show policy <name>`'s hits (#538): per route-map a policy is in - its `attach_point` as `test policy ... at` says it, every place applying it (`applied_at`), the daemon that runs it - how many routes each term matched; `counted` false when the daemon reported no such map, `compiled` false for a term with no clause at that use. An empty `policy` is every policy, each map once; a policy that does not exist is `NOT_FOUND` |
| | `TestPolicy` | `test policy <p> route\|rib <prefix> ... at <use>` (#538): `attributes` describe the route, as the words `route` takes; `rib` reads it off the box instead, with `vrf` and `neighbor`. Answers the verdict, the deciding policy and term, every step, the route before and after the sets, what was assumed and what is refused there. A use or an attribute it does not take is `INVALID_ARGUMENT`, a policy that does not exist `NOT_FOUND` |
| | `Show` | any `show`, `ping`, `traceroute` or `mtr`, as text (not `monitor`: it redraws a terminal, and a call has none - the telemetry stream is the API's live view) |
| | `GetLogging` | `show cgnat logging` (#555): each log export - syslog and IPFIX over TCP, IPFIX over UDP - its collector, who carries it (the log container or the dataplane), its connection, its counters, its spill and whether it is delivering; the box-wide drop counters; the screen itself in `text`. See [The log export](#the-log-export) |
| TelemetryService | `Subscribe` | interface counters from the statistics segment at an interval, CGNAT totals, configuration events - each `Event` with the `revision` it left and the commit's `comment` (#533) - and, with `logging_events`, the log export's `log-...` events (#555) |
| | `GetHistory` | the counters the server sampled, a minute apart, for the last 24 hours (#533): interfaces, CGNAT totals, workers, memory; streamed, a sample a message |
| RouteService | `ListRoutes` | FRR's RIB as `show ip route` reads it (#533): every VRF and both families, a page at a time from one snapshot, filtered by VRF, family, prefix (and what is inside it), protocol and selected |
| | `GetRouteSummary` | `show ip route vrf all summary`: per VRF, family and protocol, the routes in the RIB and in the FIB |
| SessionService | `WhoAmI` | the account the client certificate names, and the role the VTY login gives it (#533) |
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

## Limits a client meets

* **A request a session does not answer in 300 seconds** fails with
  `UNAVAILABLE` ("no answer in 300s"); the session's process is left as it
  is, and the next request reaches it.
* **A candidate nobody has touched for 30 minutes is closed**, its process
  ended: the next call naming it is `NOT_FOUND`, and the client opens a new
  one. An account's process for operational calls is kept 5 minutes between
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
