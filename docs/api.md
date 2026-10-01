# The management API (prisma.v1)

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
| **What** | `ConfigService` (the candidate and its commit), `OperationalService` (typed reads and any `show`), `TelemetryService` (a stream of counters, CGNAT totals and configuration events). |
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
| TelemetryService | `Subscribe` | interface counters from the statistics segment at an interval, CGNAT totals, configuration events |

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
  one after the other.

## Security

* **Mutual TLS only.** The server refuses to start without
  `/etc/prisma-routing/api/server.key`, `server.crt` and `clients-ca.crt`, and
  refuses a handshake without a client certificate that CA signed.
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
```

`show policy route <prefix>` - which policy decided a route the box holds -
has no typed call: it is `Show`'s, as text, and the same answer `TestPolicy`
gives with `rib` at each use.

## How it is held

* `test/test_api_contract.py` reads the contract against the rules above
  without protoc: unique field numbers, no Rust keyword as a name, the services
  the issue asked for.
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
  role and for a client with no certificate.

**Not covered by a lab**: the host's socket and proxy in front of a running
management container (the lab runs the server directly), and a client in
Rust. The first is held by `test/test_api_configure.sh`'s stubs; the second is
the web interface's repository's to hold.
