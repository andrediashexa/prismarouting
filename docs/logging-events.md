# The box's record of events: `show log` (#588)

## TL;DR

`show log` is what happened on this box, in Prisma's words, newest last:
commits, rollbacks and `commit confirmed` expiries with who and from where;
restarts, upgrades, reboots and shutdowns asked for, by whom; and each start of
the dataplane and of the routing daemons **with its cause** - the boot, an
operator's `system dataplane restart`, an upgrade, or "unexpected" when
nothing on the box asked for it. It is one file on the log volume, bounded,
kept across reboots, written by the components that act and never scraped
from a journal - so the door calls and timer skips that fill the journal never
reach it. The API reads it with `OperationalService.ListEvents`, and
`GetSystem` carries why the dataplane and the routing daemons last started.

FRR's `show logging` is unchanged: each routing daemon's logging
*configuration*, as before. Phase 1 is delivered in 0.151.14; the issue stays
open for phases 2 and 3 below.

> The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT** and **MAY**
> in this document are to be interpreted as described in
> [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## 5W2H

| | |
|---|---|
| **What** | A record of events on the box: time, severity, source, one sentence, and who and from where when known. `show log` and `ListEvents` read it. |
| **Why** | On 2026-10-05 the operator found the VTY's logs "totally useless": `show logging` printed FRR's per-daemon logging configuration, and the journal on 10.40.200.166 had "door: uid 1000: hostname -> 0" every 2 s and a confirm timer's skip every 16 s. The same day prisma-web said "the dataplane restarted by itself" about an upgrade, because the box recorded no restart cause. |
| **Who** | Written by the code that acts: the VTY and the API (as the account), the host's apply (`prismash --load`, `--reapply`, `--confirm-expired`), the upgrade. Read by every account. |
| **When** | At the moment of the action; a restart's cause before the restart. |
| **Where** | `/var/log/prisma-routing/events.log` (and `events.log.1`), on the log volume. |
| **How** | One JSON object per line, appended in one write; through the host's door from the management container, locally on the host. |
| **How much** | 256 KiB per file, two files: about 3000 events, years of commits and restarts. |

## The phases

| Phase | What | State |
|---|---|---|
| **1** | The record itself, bounded and kept across reboots. Commits, rollbacks, `commit confirmed` confirmed and expired, with the account and where the session came from. Upgrades (asked, and done). Reboots and shutdowns asked from the VTY or the API. Dataplane and routing starts with their cause, the cause recorded before the action. `show log [last <n>] [since <time>] [severity <level>] [source <name>]`, and the box's `| match`. `ListEvents`, and the cause in `GetSystem`. | *delivered in 0.151.14* |
| **2** | What the box sees happen rather than what it is told: interface and link up/down, SFP inserted, removed or refused (#574's card states); BGP and BFD sessions up/down with the reason (bfdd's diagnostic, BGP's last error); CGNAT pool or block exhaustion and the log exporter losing or regaining its collector (the plugin's events and `logstate`'s verdicts); logins, failed logins and logouts (sshd and the VTY's broker); SNMP and API settings applied or refused; the dataplane giving up and its card given back (`watch-the-dataplane`). Each from a watcher that compares a reading with the last one, as `observe_starts()` does - never from a log line. | planned |
| **3** | Where the record goes and how it is followed: `system logging` in Prisma's terms (a remote syslog, its level, which sources), replacing FRR's per-daemon `log syslog` as what an operator configures; `monitor log`, a follow mode like `monitor cgnat logging`, and a streaming `WatchEvents` in the API; SNMP notifications for the events PRISMA-ROUTING-MIB has an object for (#507's traps). | planned |

## How it is written

**By the code that acts, never from a journal.** Each writer calls
`events.record()` with one event. Nothing reads the journal or FRR's logs to
make events, so routine noise has no way in: the doors daemon's per-request
line, a timer that finds nothing to do, FRR's debug - none of them is a call to
`record()`. The doors daemon does not journal the events' own requests either
(`doors.journaled()`): a `show log` or a polling `GetSystem` would otherwise add
door lines of their own.

**One line, one write, bounded.** `append_event()` writes the event as one JSON
line with one `write(2)` under `O_APPEND`, so two writers interleave whole
lines. When the line would take the file past 256 KiB the file becomes
`events.log.1` (replacing the previous one) and a new file starts; the reader
reads both, oldest first. A torn last line after a power cut is skipped.

**From the management container, through the host's door - not the mount.**
The container does mount `/var/log/prisma-routing`, but the VTY in it runs as
the account that logged in, and the directory is root's 0750 because the CGNAT
trail beside this file names subscribers. Widening it would widen it for the
trail too, and an account writing the file directly could write any name in
it. So the door takes two requests (`doors.answer_event()`):

* `{"events": filters}` - any account reads, filtered on the host;
* `{"event": {...}}` - any account writes, **stamped with its name as the
  kernel reports it (SO_PEERCRED) and with the door's time**. An account
  writes only what it did - a commit, a rollback, a confirmation, an expiry,
  a request - from the VTY or the API; a start, a boot or an upgrade done is
  root's. A request that restarts something (an "asked" naming components) is
  written for an account only if it may restart things: prisma-operator, as
  the restart's own door decides (#563).

With no daemon listening - the host's own VTY, a lab - the write is local, as
every door falls back.

**Who and from where.** `who` is the account (`vppapi.identity()`, then the
door's stamp). `from` is `ssh <address>` - sshd's `SSH_CONNECTION` on the
host, and in the container the address the login's broker hands the session
door (`prisma-vty-session` sends it, `doors.session_argv()` passes it only if
it is an address) - or `the API` for an API worker (`PRISMA_EVENT_SOURCE`), or
`the box` for the host's own actions. It is the session's assertion, like the
CGNAT trail's `audit_user` (#22): a record of which operator did what among
people trusted to be there, not evidence against them.

**The configuration trail is not duplicated.** `config-history.log` is the
dataplane's hash-chained record of every pool definition, for a court order
(docs/logging.md). The commit event says that a commit happened, by whom, with
its comment; it does not copy definitions, and `show cgnat history` stays where
they are read.

## A restart's cause

**Recorded before the action, matched after it.** Whoever asks for a restart
writes an "asked" event first, naming the cause a start will carry and the
components it restarts:

| Asked by | Cause | Components |
|---|---|---|
| `system dataplane restart` (VTY, or `RestartDataplane`) | `operator` | dataplane, routing - the whole stack goes down (prisma-hot-card) |
| `system upgrade <version>` (VTY, or `Upgrade`) | `upgrade` | dataplane, routing - for whatever its own run restarts |
| `system reboot`, `system shutdown` | `reboot`, `shutdown` | none: the boot after it is the boot, and its reason is `last-reboot` (#534) |

The host's apply - `prismash --load`, which runs at boot and after every
restart of the stack, and `prismash --reapply`, which `watch-the-dataplane`
runs when Docker restarted a container - then reads the clocks `show system
uptime` reads and calls `observe_starts()`. For a start it has not recorded
(`events.state` keeps the last ones), it writes a "start" event, at the time of
the start, whose cause is:

1. `boot`, when the start is within 300 s of the kernel's boot (with the
   boot's recorded reason, when there is one);
2. else the newest "asked" naming the component, at most 900 s before the start
   and no more than 3 s after it, and after the component's previous start -
   one request is one start's cause (the lab killed a dataplane 8 s after an
   operator's restart had brought it up, and with the window alone that start
   was the operator's too);
3. else `unexpected`: nothing on the box asked for it. A warning.

The boot itself is an event too, with its reason as recorded.

**Not twice.** The VTY in the container and the API record the request as the
account that asked, then open the door that runs it on the host - and the
host's VTY, root's, running it there would record it again as root's. The door
starts that VTY with `PRISMA_ASKED_RECORDED=1` (`doors.api_run_argv()`), and
`record_asked()` writes nothing under it. A match by time was the first cut:
`lab/api-grpc.sh` showed it wrong, since root's reboot a minute after opsman's
is a request of its own.

**What it does not guess.** A box upgraded onto 0.151.14 has no `events.state`:
its first reading records the boot (a fact) and any start long after it only as
seen, because whatever caused it happened before anything was recording. Two
starts of one component less than 3 s apart read as one. An upgrade, as shipped
today, restarts neither the dataplane nor the routing daemons (they change
images at the next restart, #528), so its "asked" names them only for a start
within 900 s that nothing more recent explains.

`show system uptime` adds a `started by:` row under the dataplane and the
routing daemons when the record has their start; `GetSystem` carries the same
as `dataplane_start_cause` and `control_plane_start_cause` (`""` when the box
has not recorded that start). A client SHOULD say "restarted by itself" only
for `unexpected`.

## Reading it

```
prisma> show log
prisma> show log last 20
prisma> show log since 2h
prisma> show log since 2026-10-05
prisma> show log severity warning
prisma> show log source dataplane
prisma> show log since 1d source vty | match rollback
```

With neither `last` nor `since`, the last 50. `since` takes `45s`, `30m`,
`1h`, `2d`, `1w` or an ISO date or time (`2026-10-05`, `2026-10-05T14:00`, with
`Z` or an offset; none is the box's own zone, which is what the times print
in). `severity` is `error`, `warning`, `notice` or `info`, and shows that level
and every one above it. `source` is `vty`, `api`, `host`, `dataplane` or
`routing`. An event wider than the console folds, its continuation indented,
as every row the VTY prints does (test_prismash_refusals.py holds them to 79
columns): the first row carries the time, severity, source and the start of
the sentence, so `| match` finds an event by those; `source` and `severity`
select whole events. One unfolded line per event was the first cut, and the
width reader refused it - a 113-column row is folded by the operator's window,
inside the words. `ListEvents` gives each event's sentence whole.

A session from the lab (`lab/show-log.sh` through `tools/run-all-labs.sh`,
2026-10-06; the lab's container runs in UTC):

```
2026-09-24 09:23:19  notice   host       the box booted; the reason recorded:
    not recorded
2026-10-06 07:43:23  notice   vty        configuration committed: first pool
    (by root)
2026-10-06 07:43:25  notice   vty        configuration committed (by root)
2026-10-06 07:43:26  notice   vty        rollback to revision 1 committed (by
    root)
2026-10-06 07:43:26  notice   vty        configuration committed, to be
    confirmed within 1 minute(s) or rolled back (by root)
2026-10-06 07:43:27  warning  host       a commit confirmed 1 was not confirmed
    in time: the configuration before it was put back (from the box)
2026-10-06 07:43:29  notice   vty        system dataplane restart asked (by
    root)
2026-10-06 07:43:30  notice   dataplane  the dataplane started again: system
    dataplane restart asked by root
2026-10-06 07:43:30  notice   routing    the routing daemons (FRR) started
    again: system dataplane restart asked by root
2026-10-06 07:43:37  warning  dataplane  the dataplane started again and
    nothing on the box asked for it: unexpected
```

`ListEvents` takes the same four filters (`last` 0 is the default, as at the
prompt), answers each event as fields - `time`, `severity`, `source`, `text`,
`account`, `origin`, `kind`, `component`, `cause` - and `show log`'s screen
for the same filters in `text`. INVALID_ARGUMENT, in the VTY's words, for a
filter the VTY refuses.

## How it is tested

* `test/test_events.py` - the record, its bound, the filters, the cause, the
  door's stamping and what an account may write, the journal left alone.
* `test/test_prismash_units.py`, `test/test_prismash_confirm.py` - `show log`
  through the VTY's own dispatch and pipe, the commit, rollback, confirmation
  and expiry events, the restart asked, the start observed and its cause in
  `show system uptime`.
* `test/test_apiserver.py`, `test/test_doors.py`, `test/test_vty_session.py`
  - ListEvents' filters and fields, the cause in GetSystem, the API's request
  recorded before the host is asked, the session's address.
* `lab/show-log.sh` - commits, a rollback, an expiry, a `system dataplane
  restart` and a dataplane killed and started again, read back with every
  filter; its off run points the record at a path nothing can write.
* `lab/api-grpc.sh`'s phase for #588 - ListEvents against what the API's own
  phases did, phase 5's requests through the real doors daemon, and the cause
  in GetSystem.

### The mutations, each shown red and put back

Measured on 2026-10-06 by applying each to the tree, running the suite named,
and restoring the file.

| Mutation | Red in |
|---|---|
| `append_event()` without the rotation | test_events.py: the file grew past the cap |
| `severity_rank()` inverted | test_events.py: `severity warning` let `notice` through |
| `cause_of()` never reading the "asked" events | test_events.py: 5 checks, every cause `unexpected` |
| `cause_of()` without `after` | test_events.py: a second start took the first one's request |
| `answer_event()` keeping the caller's `who` | test_events.py: an account's event said `root` |
| `journaled()` always true | test_events.py: the events' door calls reached the journal |
| an account may write a start | test_events.py: `event_error()` let a VTY "start" through |
| `observe_starts()` guessing a cause for a start before the record | test_events.py: a first reading said `unexpected` |
| `format_event()` not folding | test_events.py: a row past 79 columns |
| `record_asked()` ignoring `PRISMA_ASKED_RECORDED` | test_prismash_units.py: the host's run recorded the request again |
| `show_or_pass()` without the `show log` branch | test_prismash_units.py: 4 checks, the line went to FRR |
| `uptime_rows()` without the `started by:` row | test_prismash_units.py |
| `_do_commit()` not recording the commit | test_prismash_confirm.py: 3 checks |
| `do_rollback()` carrying `True`, not the revision | test_prismash_confirm.py: "rollback to revision True" |
| `revert_confirm()` not recording the expiry | test_prismash_confirm.py |
| `uptime_fields()` without the causes | test_apiserver.py |
| `hostrun()` not recording the request | test_apiserver.py |
| `session_argv()` passing a `from` that is not an address | test_doors.py |
| the door's run of an API request not marked as recorded | test_doors.py |
| `appliance/upgrade`'s record line replaced by `true`; and the line before it without `\|\| true` | test_upgrade_artifact.sh: no upgrade in the box's record; the second killed the upgrade itself under `set -e`, which is how it was found |
| the contract reader without the check for a name defined twice, over a proto with a second `Event` | test_api_contract.py (the shape the first lab run of this phase met: protoc refused the contract, no stubs) |
